# -*- coding: utf-8 -*-
"""核心逻辑测试:用一个假装的 VLC 遥控服务器验证按键行为(不需要真 VLC)。"""

import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vlc_booster import VLCRemote, KeyHandler  # noqa: E402


class FakeVLC(BaseHTTPRequestHandler):
    state = {"rate": 1.0, "rates": [], "seeks": []}

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        cmd = q.get("command", [None])[0]
        if cmd == "rate":
            val = float(q["val"][0])
            self.state["rate"] = val
            self.state["rates"].append(val)
        elif cmd == "seek":
            self.state["seeks"].append(q["val"][0])
        body = ("<root><rate>%g</rate></root>" % self.state["rate"]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class KeyHandlerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeVLC)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeVLC.state.update({"rate": 1.0, "rates": [], "seeks": []})
        vlc = VLCRemote(port=self.port, password="whatever")
        self.handler = KeyHandler(vlc, boost_rate=3.0, seek_seconds=5, hold_ms=120)

    def test_short_press_right_seeks_forward(self):
        self.handler.right_down()
        time.sleep(0.05)  # 很快松开,算单击
        self.handler.right_up()
        time.sleep(0.1)
        self.assertEqual(FakeVLC.state["seeks"], ["+5"])
        self.assertEqual(FakeVLC.state["rates"], [])

    def test_long_press_boosts_then_restores_previous_rate(self):
        FakeVLC.state["rate"] = 1.25  # 假设原本在用 1.25 倍速
        self.handler.right_down()
        time.sleep(0.3)  # 按住超过阈值,算长按
        self.assertIn(3.0, FakeVLC.state["rates"])
        self.handler.right_up()
        time.sleep(0.1)
        self.assertEqual(FakeVLC.state["rates"][-1], 1.25)  # 恢复原速
        self.assertEqual(FakeVLC.state["seeks"], [])  # 长按不触发跳转

    def test_left_arrow_seeks_backward(self):
        self.handler.left_down()
        self.handler.left_up()
        time.sleep(0.1)
        self.assertEqual(FakeVLC.state["seeks"], ["-5"])

    def test_silent_release_restores_rate_without_seek(self):
        self.handler.right_down()
        time.sleep(0.3)
        self.handler.release_right_silently()  # 模拟长按中切换了窗口
        time.sleep(0.1)
        self.assertEqual(FakeVLC.state["rate"], 1.0)
        self.assertEqual(FakeVLC.state["seeks"], [])

    def test_left_repeat_ignored(self):
        self.handler.left_down()
        self.handler.left_down()  # 按住产生的重复按下
        self.handler.left_up()
        time.sleep(0.1)
        self.assertEqual(FakeVLC.state["seeks"], ["-5"])  # 只跳一次


if __name__ == "__main__":
    unittest.main()
