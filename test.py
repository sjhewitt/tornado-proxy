#!/usr/bin/env python

import os
import shutil
import subprocess
import sys
import tempfile
import urllib
import time
import unittest
import urllib2

import tornado.httpclient
import tornado.ioloop
import tornado.testing

sys.path.append('../')
from tornado_proxy import run_proxy
from tornado_proxy.cache import WaybackFileSystemCache


class TestStandaloneProxy(unittest.TestCase):
    def setUp(self):
        self.proxy = subprocess.Popen(['python', 'tornado_proxy/proxy.py',
            '8888'])
        proxy_support = urllib2.ProxyHandler({
            "https": "http://localhost:8888",
            "http": "http://localhost:8888"
        })
        opener = urllib2.build_opener(proxy_support)
        urllib2.install_opener(opener)
        # make sure the subprocess started listening on the port
        time.sleep(1)

    def tearDown(self):
        os.kill(self.proxy.pid, 15)
        time.sleep(1)
        os.kill(self.proxy.pid, 9)

    def test(self):
        base_url = '//httpbin.org/'
        urllib2.urlopen('https:' + base_url + 'get').read()
        urllib2.urlopen('http:' + base_url + 'get').read()
        urllib2.urlopen('https:' + base_url + 'post', '').read()
        urllib2.urlopen('http:' + base_url + 'post', '').read()


class TestTornadoProxy(unittest.TestCase):
    def setUp(self):
        self.ioloop = tornado.ioloop.IOLoop.instance()
        run_proxy(8889, start_ioloop=False)

    def tearDown(self):
        pass

    def test(self):
        def handle_response(resp):
            self.assertIsNone(resp.error)
            self.ioloop.stop()

        tornado.httpclient.AsyncHTTPClient.configure(
            "tornado.curl_httpclient.CurlAsyncHTTPClient")
        client = tornado.httpclient.AsyncHTTPClient()

        req = tornado.httpclient.HTTPRequest('http://httpbin.org/',
            proxy_host='127.0.0.1', proxy_port=8889)
        client.fetch(req, handle_response)
        self.ioloop.start()


class TestWaybackProxy(tornado.testing.AsyncTestCase):
    def setUp(self):
        super(TestWaybackProxy, self).setUp()
        self.cache_dir = tempfile.mkdtemp('-wayback')
        self.cache = WaybackFileSystemCache(self.cache_dir)
        run_proxy(8889, start_ioloop=False, cache=self.cache)

    def tearDown(self):
        super(TestWaybackProxy, self).tearDown()
        shutil.rmtree(self.cache_dir)

    @tornado.testing.gen_test
    def test(self):
        tornado.httpclient.AsyncHTTPClient.configure(
            "tornado.curl_httpclient.CurlAsyncHTTPClient")
        client = tornado.httpclient.AsyncHTTPClient()

        # first set the response to a value
        req = tornado.httpclient.HTTPRequest(
            "http://respondto.it/test-tornado-proxy?view",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method='POST', body=urllib.urlencode({
                "json": "{\"foo\": \"baz\"}",
                "xml": ""
            }))
        response = yield client.fetch(req)
        req = tornado.httpclient.HTTPRequest(
            "http://respondto.it/test-tornado-proxy.json",
            proxy_host='127.0.0.1', proxy_port=8889)
        response = yield client.fetch(req)
        self.assertEqual(response.body, "{\"foo\": \"baz\"}")
        cache_key = response.headers['X-Proxy-Cache-Key']

        # now change the value
        req = tornado.httpclient.HTTPRequest(
            "http://respondto.it/test-tornado-proxy?view",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method='POST', body=urllib.urlencode({
                "json": "{\"bar\": \"foo\"}",
                "xml": "    "
            }))
        response = yield client.fetch(req)

        # and the value shouldn't have changed
        req = tornado.httpclient.HTTPRequest(
            "http://respondto.it/test-tornado-proxy.json",
            proxy_host='127.0.0.1', proxy_port=8889)
        response = yield client.fetch(req)
        self.assertEqual(response.body, "{\"foo\": \"baz\"}")
        self.assertEqual(response.headers['X-Proxy-Cache-Key'], cache_key)


if __name__ == '__main__':
    unittest.main()
