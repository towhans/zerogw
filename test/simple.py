from http import client as http
import socket
import subprocess
import unittest
import time
import os

import zmq

ZEROGW_BINARY="./build/zerogw"
CONFIG="./test/zerogw.yaml"

ECHO_SOCKET = "ipc:///tmp/zerogw-test-echo"
CHAT_FW = "ipc:///tmp/zerogw-test-chatfw"
CHAT_SOCK = "ipc:///tmp/zerogw-test-chat"
MINIGAME = "ipc:///tmp/zerogw-test-minigame"

HTTP_ADDR = "/tmp/zerogw-test"
STATUS_ADDR = "ipc:///tmp/zerogw-test-status"

class Base(unittest.TestCase):

    def setUp(self):
        for i in (HTTP_ADDR,):
            try:
                os.unlink(i)
            except OSError:
                pass
        self.proc = subprocess.Popen([ZEROGW_BINARY, '-c', CONFIG])

    def http(self):
        conn = http.HTTPConnection('localhost', timeout=1.0)
        conn.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        for i in range(100):
            try:
                conn.sock.connect(HTTP_ADDR)
            except socket.error:
                time.sleep(0.1)
                continue
            else:
                break
        else:
            raise RuntimeError("Can't connect to zerogw")
        return conn

    def tearDown(self):
        self.proc.terminate()
        self.proc.wait()

class HTTP(Base):

    def testHttp(self):
        conn = self.http()
        conn.request('GET', '/crossdomain.xml')
        resp = conn.getresponse()
        self.assertTrue('cross-domain-policy', resp.read())
        conn.close()

class WebSocket(Base):

    def testConnAndClose(self):
        conn = self.http()
        conn.request('GET', '/chat?action=CONNECT')
        id = conn.getresponse().read().decode('ascii')
        self.assertTrue(id)
        conn.request('GET', '/chat?timeout=0&id='+id)
        resp = conn.getresponse()
        self.assertEqual(resp.getheader('X-Messages'), '0')
        self.assertEqual(resp.read(), b'')
        conn.request('GET', '/chat?action=CLOSE&id=' + id)
        resp = conn.getresponse()
        self.assertEqual(resp.getheader('X-Connection'), 'close')
        conn.close()

class CheckingWebsock(object):

    def __init__(self, testcase):
        self.testcase = testcase
        self.http = testcase.http()
        self.ack = ''

    def connect(self):
        self.http.request('GET', '/chat?action=CONNECT')
        self.id = self.http.getresponse().read().decode('utf-8')
        val = self.testcase.backend_recv()
        self.testcase.assertEqual(val[1], b'connect')
        self.intid = val[0]

    def client_send(self, body):
        self.http.request("GET",
            '/chat?limit=0&timeout=0&id=' + self.id, body=body)
        self.testcase.assertEqual(b'', self.http.getresponse().read())
        self.testcase.assertEqual(self.testcase.backend_recv(),
            [self.intid, b'message', body.encode('utf-8')])

    def client_got(self, body):
        body = body.encode('utf-8')
        self.http.request("GET",
            '/chat?limit=1&timeout=1.0&ack='+self.ack+'&id=' + self.id)
        resp = self.http.getresponse()
        self.ack = resp.getheader('X-Message-ID')
        self.testcase.assertEqual(resp.read(), body)

    def subscribe(self, topic):
        self.testcase.backend_send(
            'subscribe', self.intid, topic)

    def unsubscribe(self, topic):
        self.testcase.backend_send(
            'unsubscribe', self.intid, topic)

    def close(self):
        self.http.request('GET', '/chat?action=CLOSE&id=' + self.id)
        resp = self.http.getresponse()
        self.testcase.assertEqual(resp.getheader('X-Connection'), 'close')
        self.http.close()

class Chat(Base):
    timeout = 1  # in zmq.select units (seconds)

    def setUp(self):
        self.zmq = zmq.Context(1)
        super().setUp()
        self.chatfw = self.zmq.socket(zmq.PULL)
        self.chatfw.connect(CHAT_FW)
        self.chatout = self.zmq.socket(zmq.PUB)
        self.chatout.connect(CHAT_SOCK)
        time.sleep(0.2)

    def backend_send(self, *args):
        self.assertEqual(([], [self.chatout], []),
            zmq.select([], [self.chatout], [], timeout=self.timeout))
        self.chatout.send_multipart([
            a if isinstance(a, bytes) else a.encode('utf-8')
            for a in args])

    def backend_recv(self):
        self.assertEqual(([self.chatfw], [], []),
            zmq.select([self.chatfw], [], [], timeout=self.timeout))
        val =  self.chatfw.recv_multipart()
        if val[1] == b'heartbeat':
            return self.backend_recv()
        return val

    def websock(self):
        return CheckingWebsock(self)

    def tearDown(self):
        self.chatout.close()
        self.chatfw.close()
        super().tearDown()

    def testSimple(self):
        ws = self.websock()
        ws.connect()
        ws.client_send('hello_world')
        ws.subscribe('chat')
        self.backend_send('publish', 'chat', 'message: hello_world')
        ws.client_got('message: hello_world')
        self.backend_send('publish', 'chat', 'message: another_hello')
        ws.client_got('message: another_hello')
        ws.unsubscribe('chat')
        self.backend_send('publish', 'chat', 'message: silent')
        self.backend_send('send', ws.intid, 'message: personal')
        ws.client_got('message: personal')
        ws.client_send('hurray!')
        ws.close()

if __name__ == '__main__':
    unittest.main()