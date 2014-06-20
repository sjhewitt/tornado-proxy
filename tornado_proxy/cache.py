import hashlib
import logging
import time
import gzip
import os.path
import json
import sqlite3
from collections import namedtuple, MutableMapping
from tornado.httputil import HTTPHeaders
from tornado.httpclient import HTTPError

logger = logging.getLogger("tornado.proxy.cache")


class Cache(MutableMapping):
    def hash_request(self, request):
        hash = hashlib.md5()
        hash.update(request.url)
        hash.update(request.method)
        hash.update(request.body)
        return hash.hexdigest()

    def __contains__(self, request):
        key = self.hash_request(request)
        contains = self._contains(key)
        logger.info('Checking if request %s is in cache: %s', key, contains)
        return contains

    def __getitem__(self, request):
        key = self.hash_request(request)
        logger.info('Returning request %s from cache', key)
        return self._get(key)

    def __setitem__(self, request, response):
        key = self.hash_request(request)
        logger.info('Putting request %s into cache', key)
        self._set(key, response)

    def __delitem__(self, request):
        raise NotImplementedError

    def __iter__(self):
        raise NotImplementedError

    def __len__(self):
        raise NotImplementedError


class SimpleCache(Cache):
    def __init__(self):
        self.data = {}

    def _contains(self, key):
        return key in self.data

    def _get(self, key):
        return self.data[key]

    def _set(self, key, val):
        self.data[key] = val


HTTPResponse = namedtuple('HTTPResponse', ['url', 'error', 'code', 'headers', 'body'])


class FileSystemCache(Cache):

    def __init__(self, root):
        self.root = root

    def hash_request(self, request):
        hash = super(FileSystemCache, self).hash_request(request)
        return os.path.join(self.root, hash[0:2], hash[2:4], hash + '.gz')

    def _contains(self, key):
        return os.path.exists(key)

    def _get(self, key):
        try:
            with gzip.open(key, 'rb') as f:
                url = f.readline()
                code, message = f.readline().split(',', 1)
                code = int(code)
                if message:
                    error = HTTPError(code, message)
                else:
                    error = None
                headers = HTTPHeaders(json.loads(f.readline()))
                body = f.read()
            return HTTPResponse(url, error, code, headers, body)
        except IOError:
            raise KeyError

    def _set(self, key, val):
        d = os.path.dirname(key)
        if not os.path.exists(d):
            os.makedirs(d)
        with gzip.open(key, 'wb') as f:
            f.write(val.request.url)
            f.write('\n')
            if val.error:
                f.write(unicode(val.error.code))
                f.write(',')
                f.write(val.error.message)
            else:
                f.write(unicode(val.code))
                f.write(',')
            f.write('\n')
            f.write(json.dumps(val.headers))
            f.write('\n')
            f.write(val.body)


class WaybackFileSystemCache(FileSystemCache):

    def __init__(self, root, db_file='wayback.db', default_within=86400):
        super(WaybackFileSystemCache, self).__init__(root)
        db_file = os.path.join(root, 'wayback.db')
        create_tables = not os.path.exists(db_file)
        self.db = sqlite3.connect(db_file)
        self.default_within = default_within
        if create_tables:
            self._create_tables()

    def _create_tables(self):
        c = self.db.cursor()
        c.execute("CREATE TABLE idx (key text, timestamp integer);")
        c.execute("CREATE INDEX key_timestamp ON idx (key, timestamp)")
        self.db.commit()

    def hash_request(self, request):
        path = getattr(request, "_wb_path", None)
        if path:
            return path
        request._wb_hash = Cache.hash_request(self, request)
        c = self.db.cursor()
        now = int(time.time())
        request_time = request.headers.pop('X-Wayback-Timestamp', None)
        within = request.headers.pop('X-Wayback-Within', None)
        error_on_miss = False
        if within:
            within = int(within)
        if request_time:
            request_time = int(request_time)
            if within:
                # if request_time and within are specified, we want to get a
                # version that is between those 2 values. this should raise an
                # error if there's a miss!
                args = (request_time, request_time - within)
                f = "timestamp < ? AND timestamp > ?"
                error_on_miss = True
            else:
                # otherwise we just want any version that's before the
                # specified timestamp
                f = "timestamp < ?"
        else:
            # if no request tiem was specified, we default to finding a page
            # within the specified or default time range
            if not within:
                within = self.default_within
            args = (now - within, )
            f = "timestamp > ?"

        c.execute("""SELECT timestamp FROM idx WHERE
                key=? AND {}
                ORDER BY timestamp desc
                LIMIT 1;""".format(f), (request._wb_hash, ) + args)
        val = c.fetchone()
        if val:
            if error_on_miss:
                raise "Fail!!!"
            request._wb_insert = False
            request._wb_timestamp = val[0]
        else:
            request._wb_insert = True
            request._wb_timestamp = now
        request._wb_path = os.path.join(
            self.root, request._wb_hash[0:2], request._wb_hash[2:4],
            request._wb_hash + '-' + str(request._wb_timestamp) + '.gz')
        return request._wb_path

    def __setitem__(self, request, response):
        super(WaybackFileSystemCache, self).__setitem__(request, response)
        if request._wb_insert:
            logger.info("inserting into index")
            c = self.db.cursor()
            c.execute(
                "INSERT INTO idx (key, timestamp) VALUES (?, ?);",
                (request._wb_hash, request._wb_timestamp))
            self.db.commit()
