# encoding=utf-8
import functools
import os.path
import socket
import ssl
import sys
import tornado.testing
import tornado.web

from wpull.backport.testing import unittest
from wpull.errors import (ConnectionRefused, SSLVerficationError, NetworkError,
                          ProtocolError, NetworkTimedOut)
from wpull.http.client import Client
from wpull.http.connection import (Connection, ConnectionPool,
                                   HostConnectionPool, ConnectionParams)
from wpull.http.request import Request, Response
from wpull.http.util import parse_charset
from wpull.recorder import DebugPrintRecorder
from wpull.testing.badapp import BadAppTestCase


DEFAULT_TIMEOUT = 30


class TestClient(BadAppTestCase):
    def setUp(self):
        super().setUp()
        tornado.ioloop.IOLoop.current().set_blocking_log_threshold(0.5)

    @tornado.testing.gen_test(timeout=DEFAULT_TIMEOUT)
    def test_connection_pool_min(self):
        connection_pool = ConnectionPool()
        client = Client(connection_pool)

        for dummy in range(2):
            response = yield client.fetch(
                Request.new(self.get_url('/sleep_short')))
            self.assertEqual(200, response.status_code)
            self.assertEqual(b'12', response.body.content)

        self.assertEqual(1, len(connection_pool))
        connection_pool_entry = list(connection_pool.values())[0]
        self.assertIsInstance(connection_pool_entry, HostConnectionPool)
        self.assertEqual(1, len(connection_pool_entry))

    @tornado.testing.gen_test(timeout=DEFAULT_TIMEOUT)
    def test_connection_pool_max(self):
        connection_pool = ConnectionPool()
        client = Client(connection_pool)
        requests = [client.fetch(
            Request.new(self.get_url('/sleep_short'))) for dummy in range(6)]
        responses = yield requests

        for response in responses:
            self.assertEqual(200, response.status_code)
            self.assertEqual(b'12', response.body.content)

        self.assertEqual(1, len(connection_pool))
        connection_pool_entry = list(connection_pool.values())[0]
        self.assertIsInstance(connection_pool_entry, HostConnectionPool)
        self.assertEqual(6, len(connection_pool_entry))

    @tornado.testing.gen_test(timeout=DEFAULT_TIMEOUT)
    def test_connection_pool_over_max(self):
        connection_pool = ConnectionPool()
        client = Client(connection_pool)
        requests = [client.fetch(
            Request.new(self.get_url('/sleep_short'))) for dummy in range(12)]
        responses = yield requests

        for response in responses:
            self.assertEqual(200, response.status_code)
            self.assertEqual(b'12', response.body.content)

        self.assertEqual(1, len(connection_pool))
        connection_pool_entry = list(connection_pool.values())[0]
        self.assertIsInstance(connection_pool_entry, HostConnectionPool)
        self.assertEqual(6, len(connection_pool_entry))

    @tornado.testing.gen_test(timeout=DEFAULT_TIMEOUT)
    def test_connection_pool_clean(self):
        connection_pool = ConnectionPool()
        client = Client(connection_pool)
        requests = [client.fetch(
            Request.new(self.get_url('/'))) for dummy in range(12)]
        responses = yield requests

        for response in responses:
            self.assertEqual(200, response.status_code)

        connection_pool.clean()

        self.assertEqual(0, len(connection_pool))

    @tornado.testing.gen_test(timeout=DEFAULT_TIMEOUT)
    def test_client_exception_throw(self):
        client = Client()

        try:
            yield client.fetch(Request.new('http://wpull-no-exist.invalid'))
        except NetworkError:
            pass
        else:
            self.fail()

    @tornado.testing.gen_test(timeout=DEFAULT_TIMEOUT)
    def test_client_exception_recovery(self):
        connection_factory = functools.partial(
            Connection, params=ConnectionParams(read_timeout=0.2)
        )
        host_connection_pool_factory = functools.partial(
            HostConnectionPool, connection_factory=connection_factory)
        connection_pool = ConnectionPool(host_connection_pool_factory)
        client = Client(connection_pool)

        for dummy in range(7):
            try:
                yield client.fetch(
                    Request.new(self.get_url('/header_early_close')),
                    recorder=DebugPrintRecorder()
                )
            except NetworkError:
                pass
            else:
                self.fail()

        for dummy in range(7):
            response = yield client.fetch(Request.new(self.get_url('/')))
            self.assertEqual(200, response.status_code)


class TestHTTP(unittest.TestCase):
    def test_request(self):
        request = Request.new('http://example.com/robots.txt')
        self.assertEqual(
            (b'GET /robots.txt HTTP/1.1\r\n'
             b'Host: example.com\r\n'
             b'\r\n'),
            request.header()
        )

    def test_request_port(self):
        request = Request.new('https://example.com:4567/robots.txt')
        self.assertEqual(
            (b'GET /robots.txt HTTP/1.1\r\n'
             b'Host: example.com:4567\r\n'
             b'\r\n'),
            request.header()
        )

    def test_parse_charset(self):
        self.assertEqual(
            None,
            parse_charset('text/plain')
        )
        self.assertEqual(
            None,
            parse_charset('text/plain; charset=')
        )
        self.assertEqual(
            'utf_8',
            parse_charset('text/plain; charset=utf_8')
        )
        self.assertEqual(
            'UTF-8',
            parse_charset('text/plain; charset="UTF-8"')
        )
        self.assertEqual(
            'Utf8',
            parse_charset("text/plain; charset='Utf8'")
        )
        self.assertEqual(
            'UTF-8',
            parse_charset('text/plain; CHARSET="UTF-8"')
        )

    def test_parse_status_line(self):
        version, code, msg = Response.parse_status_line(b'HTTP/1.0 200 OK')
        self.assertEqual('HTTP/1.0', version)
        self.assertEqual(200, code)
        self.assertEqual('OK', msg)

        version, code, msg = Response.parse_status_line(
            b'HTTP/1.0 404 Not Found')
        self.assertEqual('HTTP/1.0', version)
        self.assertEqual(404, code)
        self.assertEqual('Not Found', msg)

        version, code, msg = Response.parse_status_line(b'HTTP/1.1  200   OK')
        self.assertEqual('HTTP/1.1', version)
        self.assertEqual(200, code)
        self.assertEqual('OK', msg)

        version, code, msg = Response.parse_status_line(b'HTTP/1.1  200')
        self.assertEqual('HTTP/1.1', version)
        self.assertEqual(200, code)
        self.assertEqual('', msg)

        version, code, msg = Response.parse_status_line(b'HTTP/1.1  200  ')
        self.assertEqual('HTTP/1.1', version)
        self.assertEqual(200, code)
        self.assertEqual('', msg)

        version, code, msg = Response.parse_status_line(
            'HTTP/1.1 200 ððð'.encode('latin-1'))
        self.assertEqual('HTTP/1.1', version)
        self.assertEqual(200, code)
        self.assertEqual('ððð', msg)

        self.assertRaises(
            ProtocolError,
            Response.parse_status_line, b'HTTP/1.0'
        )
        self.assertRaises(
            ProtocolError,
            Response.parse_status_line, b'HTTP/2.0'
        )

        version, code, msg = Response.parse_status_line(
            b'HTTP/1.0 404 N\x99t \x0eounz\r\n')
        self.assertEqual('HTTP/1.0', version)
        self.assertEqual(404, code)
        self.assertEqual(b'N\x99t \x0eounz'.decode('latin-1'), msg)

    def test_connection_should_close(self):
        self.assertTrue(Connection.should_close('HTTP/1.0', None))
        self.assertTrue(Connection.should_close('HTTP/1.0', 'wolf'))
        self.assertTrue(Connection.should_close('HTTP/1.0', 'close'))
        self.assertTrue(Connection.should_close('HTTP/1.0', 'ClOse'))
        self.assertFalse(Connection.should_close('HTTP/1.0', 'keep-Alive'))
        self.assertFalse(Connection.should_close('HTTP/1.0', 'keepalive'))
        self.assertTrue(Connection.should_close('HTTP/1.1', 'close'))
        self.assertTrue(Connection.should_close('HTTP/1.1', 'ClOse'))
        self.assertFalse(Connection.should_close('HTTP/1.1', 'dragons'))
        self.assertFalse(Connection.should_close('HTTP/1.1', 'keep-alive'))
        self.assertTrue(Connection.should_close('HTTP/1.2', 'close'))


class SimpleHandler(tornado.web.RequestHandler):
    def get(self):
        self.write(b'OK')


class TestSSL(tornado.testing.AsyncHTTPSTestCase):
    def get_app(self):
        return tornado.web.Application([
            (r'/', SimpleHandler)
        ])

    @tornado.testing.gen_test(timeout=DEFAULT_TIMEOUT)
    def test_ssl_fail(self):
        connection = Connection(
            ('localhost', self.get_http_port()),
            ssl_enable=True,
            params=ConnectionParams(
                ssl_options=dict(
                    cert_reqs=ssl.CERT_REQUIRED,
                    ca_certs=self.get_ssl_options()['certfile']
                )
            )
        )
        try:
            yield connection.fetch(Request.new(self.get_url('/')))
        except SSLVerficationError:
            pass
        else:
            self.fail()

    @tornado.testing.gen_test(timeout=DEFAULT_TIMEOUT)
    def test_ssl_no_check(self):
        connection = Connection(
            ('localhost', self.get_http_port()), ssl_enable=True
        )
        yield connection.fetch(Request.new(self.get_url('/')))
