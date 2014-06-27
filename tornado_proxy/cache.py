import codecs
import datetime
import gzip
import hashlib
import json
import logging
import os.path
import sqlite3
from collections import MutableMapping, namedtuple

import tornado.web
from tornado.httpclient import HTTPError, HTTPRequest
from tornado.httputil import HTTPHeaders

logger = logging.getLogger("tornado.proxy.cache")


class Cache(MutableMapping):
    def hash_request(self, request):
        hash = hashlib.md5()
        hash.update(request.url)
        hash.update(request.method)
        if request.body is not None:
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
        return self._get(request, key)

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

    def _get(self, request, key):
        return self.data[key]

    def _set(self, key, val):
        self.data[key] = val


HTTPResponse = namedtuple('HTTPResponse', ['url', 'error', 'code', 'headers', 'body'])


def get_content_charset(headers):
    """Gets the charset of the response body"""
    try:
        content_type = headers['Content-Type']
        # Example: 'application/json;charset=utf-8' -> 'utf-8'
        return content_type.split(';')[1].split('=')[1].lower()
    except (KeyError, IndexError):
        return 'latin1'


class FileSystemCache(Cache):
    """Stores responses on the filesystem

    The file format used is gzipped plain text. The first 3 lines contain the
    request/response metadata, then the rest of the file is the body of the
    response:

    REQUEST_URL
    STATUS_CODE,ERROR_MESSAGE
    HEADERS_JSON
    BODY
    """

    def __init__(self, root):
        self.root = root

    def hash_request(self, request):
        hash = super(FileSystemCache, self).hash_request(request)
        return os.path.join(hash[0:2], hash[2:4], hash + '.gz')

    def _contains(self, key):
        return os.path.exists(key)

    def _get(self, request, key):
        try:
            path = os.path.join(self.root, key)
            reader = codecs.getreader("utf-8")
            with gzip.open(path, 'rb') as _f:
                f = reader(_f)
                url = f.readline()
                code, message = f.readline().split(',', 1)
                code = int(code)
                if message:
                    error = HTTPError(code, message)
                else:
                    error = None
                headers = HTTPHeaders(json.loads(f.readline()))
                body = u''
                while True:
                    part = f.read()
                    if not part:
                        break
                    body += part
            headers['X-Proxy-Cache-Key'] = key
            charset = get_content_charset(headers)
            if charset != 'utf-8':
                body = body.encode(charset)
            return HTTPResponse(url, error, code, headers, body)
        except IOError:
            raise KeyError

    def _set(self, key, val):
        val.headers['X-Proxy-Cache-Key'] = key
        path = os.path.join(self.root, key)
        d = os.path.dirname(path)
        if not os.path.exists(d):
            os.makedirs(d)
        writer = codecs.getwriter('utf-8')
        try:
            with gzip.open(path, 'wb') as _f:
                f = writer(_f)
                try:
                    f.write(val.request.url)
                except AttributeError:
                    f.write(val.url)
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
                body = val.body
                if not isinstance(body, unicode):
                    charset = get_content_charset(val.headers)
                    body = body.decode(charset)
                f.write(body)
        except:
            logger.exception('Exception while trying to write cache file')
            os.remove(path)


class WaybackPageNotFound(Exception):
    def __init__(self, url, timestamp, within=None):
        self.url = url
        self.timestamp = timestamp
        self.within = within


class WaybackFileSystemCache(FileSystemCache):

    def __init__(self, root, db_file='wayback.db', default_within=2592000):
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
        """Uses the database index to get the hash of the request.
        This is a little bit ugly as it uses the request to store state between
        getting and setting cache values"""
        path = getattr(request, "_wb_path", None)
        if path:
            return path
        request._wb_hash = Cache.hash_request(self, request)
        now = int(datetime.datetime.utcnow().strftime("%s"))
        if hasattr(request, "_wb_force"):
            if not hasattr(request, "_wb_timestamp"):
                request._wb_timestamp = now
            request._wb_insert = True
            request._wb_path = os.path.join(
                request._wb_hash[0:2], request._wb_hash[2:4],
                request._wb_hash + '-' + str(request._wb_timestamp) + '.gz')
            return request._wb_path
        c = self.db.cursor()
        request_time = request.headers.pop('X-Wayback-Timestamp', None)
        within = request.headers.pop('X-Wayback-Within', None)
        error_on_miss = False
        if within:
            within = int(within)
        if request_time:
            request_time = int(request_time)
            error_on_miss = True
            if within == 0:
                args = (request_time, )
                f = "timestamp = ?"
            elif within:
                # if request_time and within are specified, we want to get a
                # version that is between those 2 values. this should raise an
                # error if there's a miss!
                args = (request_time, request_time - within)
                f = "timestamp <= ? AND timestamp > ?"
            else:
                # otherwise we just want any version that's before the
                # specified timestamp
                args = (request_time, )
                f = "timestamp <= ?"
        else:
            # if no request tiem was specified, we default to finding a page
            # within the specified or default time range
            if within == 0:
                args = (now, )
                f = "timestamp = ?"
            else:
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
            request._wb_insert = False
            request._wb_timestamp = val[0]
        elif error_on_miss:
            raise WaybackPageNotFound(request.url, request_time)
        else:
            request._wb_insert = True
            request._wb_timestamp = now
        request._wb_path = os.path.join(
            request._wb_hash[0:2], request._wb_hash[2:4],
            request._wb_hash + '-' + str(request._wb_timestamp) + '.gz')
        return request._wb_path

    def _set(self, key, response):
        # Provide the wayback timestamp in the response headers
        super(WaybackFileSystemCache, self)._set(key, response)
        response.headers['X-Wayback-Timestamp'] = \
            unicode(response.request._wb_timestamp)

    def _get(self, request, key):
        # Provide the wayback timestamp in the response headers
        response = super(WaybackFileSystemCache, self)._get(request, key)
        response.headers['X-Wayback-Timestamp'] = \
            unicode(request._wb_timestamp)
        return response

    def __setitem__(self, request, response):
        super(WaybackFileSystemCache, self).__setitem__(request, response)
        if request._wb_insert:
            logger.info("inserting into index")
            c = self.db.cursor()
            c.execute(
                "INSERT INTO idx (key, timestamp) VALUES (?, ?);",
                (request._wb_hash, request._wb_timestamp))
            self.db.commit()


class CacheHandler(tornado.web.RequestHandler):

    def initialize(self, cache):
        self.cache = cache

    def get(self):
        url = self.get_argument('url')
        method = self.get_argument('method', 'GET')
        request = HTTPRequest(url, method=method)
        response = self.cache.get(request)
        if not response:
            self.set_status(404)
            self.write('Page not found in cache')
            self.finish()
        self.set_status(response.code)
        for header in ('Date', 'Cache-Control', 'Server',
                       'Content-Type', 'Location',
                       'X-Proxy-Cache-Key', 'X-Wayback-Timestamp'):
            v = response.headers.get(header)
            if v:
                self.set_header(header, v)
        self.write(response.body)
        self.finish()

    def post(self):
        """
        data = {
            'request': {
                'method': 'GET',
                'url': 'http://www.google.ca',
                'body': None
            },
            'response': {
                'url': 'http://www.google.ca',
                'error': None,
                'code': 404,
                'headers': {},
                'body': 'meh'
            },
            'wayback': {
                'timestamp': 123456
            }
        }
        """
        data = json.loads(self.request.body)
        request = HTTPRequest(
            url=data['request']['url'],
            method=data['request'].get('method', 'GET'),
            body=data['request'].get('body')
        )
        request._wb_force = True
        wayback = data.get('wayback')
        if wayback:
            for k, v in wayback.iteritems():
                setattr(request, '_wb_' + k, v)
        response = HTTPResponse(
            url=data['response']['url'],
            error=data['response'].get('error'),
            code=data['response'].get('code', 200),
            headers=data['response'].get('headers', {}),
            body=data['response']['body']
        )
        self.cache[request] = response
        self.write('ok')
        self.finish()
