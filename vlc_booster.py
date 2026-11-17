#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VLC Booster —— VLC 按键增强助手 (macOS)

功能:
  · 单击 右箭头:快进 5 秒(视频/音频均可)
  · 单击 左箭头:快退 5 秒
  · 长按 右箭头(超过 0.3 秒):以 3 倍速播放,松开后恢复原来的速度
  · 空格 / 点击画面暂停播放:VLC 自带,无需本程序

原理:
  VLC 的官方插件接口无法感知"按住/松开"按键的时机,因此本程序在
  macOS 层面监听方向键(仅当 VLC 在最前台时生效),并通过 VLC 自带的
  Web 遥控接口(HTTP)控制播放。
"""

import argparse
import atexit
import base64
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

VLC_BUNDLE_ID = "org.videolan.vlc"
KEY_LEFT = 123
KEY_RIGHT = 124


# ---------------------------------------------------------------- 遥控 VLC

class VLCRemote:
    """通过 VLC 的 Web 遥控接口控制播放。"""

    def __init__(self, host="127.0.0.1", port=8080, password="vlcpassword", timeout=1.5):
        self.url = "http://%s:%d/requests/status.xml" % (host, port)
        token = base64.b64encode((":" + password).encode()).decode()
        self.headers = {"Authorization": "Basic " + token}
        self.timeout = timeout

    def _get(self, **params):
        url = self.url
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def available(self):
        try:
            self._get()
            return True
        except Exception:
            return False

    def get_rate(self):
        root = ET.fromstring(self._get())
        node = root.find("rate")
        try:
            rate = float(node.text)
        except (AttributeError, TypeError, ValueError):
            return 1.0
        return rate if rate > 0 else 1.0

    def set_rate(self, rate):
        self._get(command="rate", val="{:g}".format(rate))

    def seek(self, seconds):
        # val 支持相对值:"+5" 表示前进 5 秒,"-5" 表示后退 5 秒
        self._get(command="seek", val="{:+d}".format(seconds))

    def toggle_pause(self):
        self._get(command="pl_pause")


# ---------------------------------------------------------------- 按键状态机

class KeyHandler:
    """把方向键事件翻译成对 VLC 的控制指令。"""

    def __init__(self, vlc, boost_rate=3.0, seek_seconds=5, hold_ms=300):
        self.vlc = vlc
        self.boost_rate = boost_rate
        self.seek_seconds = seek_seconds
        self.hold_seconds = hold_ms / 1000.0
        self._lock = threading.Lock()
        self._right_down = False
        self._boosting = False
        self._prev_rate = 1.0
        self._timer = None
        self._left_down = False
        self.on_error = None  # 可选: callable(str)

    def _report_error(self, exc):
        if self.on_error:
            self.on_error(str(exc))

    # ---- 右箭头 ----
    def right_down(self):
        with self._lock:
            if self._right_down:
                return
            self._right_down = True
            timer = threading.Timer(self.hold_seconds, self._begin_boost)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _begin_boost(self):
        with self._lock:
            if not self._right_down:
                return  # 在计时结束前已松开,算单击
            self._boosting = True
        try:
            prev = self.vlc.get_rate()
            if prev > 0:
                self._prev_rate = prev
            self.vlc.set_rate(self.boost_rate)
        except Exception as exc:
            self._report_error(exc)

    def right_up(self, seek=True):
        with self._lock:
            if not self._right_down:
                return
            self._right_down = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            boosting = self._boosting
            self._boosting = False
        try:
            if boosting:
                self.vlc.set_rate(self._prev_rate)  # 恢复之前的速度
            elif seek:
                self.vlc.seek(+self.seek_seconds)   # 短按:快进
        except Exception as exc:
            self._report_error(exc)

    def release_right_silently(self):
        """焦点离开 VLC 时调用:只恢复速度,不跳转。"""
        self.right_up(seek=False)

    def is_boosting(self):
        with self._lock:
            return self._boosting

    def is_right_down(self):
        with self._lock:
            return self._right_down

    # ---- 左箭头 ----
    def left_down(self):
        with self._lock:
            self._left_down = True

    def left_up(self):
        with self._lock:
            if not self._left_down:
                return
            self._left_down = False
        try:
            self.vlc.seek(-self.seek_seconds)  # 快退
        except Exception as exc:
            self._report_error(exc)


# ---------------------------------------------------------------- 前台检测

def vlc_is_frontmost():
    try:
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return app is not None and app.bundleIdentifier() == VLC_BUNDLE_ID
    except Exception:
        return False


def vlc_is_running():
    try:
        r = subprocess.run(["pgrep", "-x", "VLC"], capture_output=True)
        if r.returncode == 0:
            return True
        if not r.stderr:  # 返回码 1 且无报错 = 确实没在运行
            return False
    except Exception:
        pass
    # pgrep 不可用时,改用 macOS 系统接口查
    try:
        from AppKit import NSWorkspace
        apps = NSWorkspace.sharedWorkspace().runningApplications()
        return any(a.bundleIdentifier() == VLC_BUNDLE_ID for a in apps)
    except Exception:
        return False


def launch_vlc(port, password):
    """以带 Web 遥控参数的方式启动 VLC。"""
    subprocess.Popen([
        "open", "-a", "VLC", "--args",
        "--extraintf", "http",
        "--http-port", str(port),
        "--http-password", password,
    ])


# ---------------------------------------------------------------- 键盘监听

def run(args):
    import Quartz
    from Quartz import (
        CFMachPortCreateRunLoopSource, CFRunLoopAddSource, CFRunLoopGetCurrent,
        CFRunLoopRun, CFRunLoopStop, CGEventGetIntegerValueField, CGEventMaskBit,
        CGEventTapCreate, CGEventTapEnable, kCGEventKeyDown, kCGEventKeyUp,
        kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput,
        kCGHeadInsertEventTap, kCGKeyboardEventAutorepeat, kCGKeyboardEventKeycode,
        kCGEventTapOptionDefault, kCGSessionEventTap, kCFRunLoopCommonModes,
    )

    vlc = VLCRemote(host=args.host, port=args.port, password=args.password)
    handler = KeyHandler(vlc, boost_rate=args.rate,
                         seek_seconds=args.seek, hold_ms=args.hold)

    last_warn = [0.0]

    def warn(msg):
        now = time.monotonic()
        if now - last_warn[0] > 5:  # 防止 VLC 关闭时刷屏
            last_warn[0] = now
            print("  [提醒] 暂时连不上 VLC(可能未在播放):", msg, flush=True)

    handler.on_error = warn
    tap_holder = {}

    def callback(proxy, event_type, event, refcon):
        # 系统可能因超时停用监听,需要重新启用
        if event_type in (kCGEventTapDisabledByTimeout,
                          kCGEventTapDisabledByUserInput):
            tap = tap_holder.get("tap")
            if tap is not None:
                CGEventTapEnable(tap, True)
            return event

        if not vlc_is_frontmost():
            return event

        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
        if keycode not in (KEY_LEFT, KEY_RIGHT):
            return event

        if event_type == kCGEventKeyDown:
            if not CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat):
                if keycode == KEY_RIGHT:
                    handler.right_down()
                else:
                    handler.left_down()
            return None  # 吞掉按键,避免 VLC 自带的绑定重复触发
        else:  # kCGEventKeyUp
            if keycode == KEY_RIGHT:
                handler.right_up()
            else:
                handler.left_up()
            return None

    mask = CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventKeyUp)
    tap = CGEventTapCreate(
        kCGSessionEventTap, kCGHeadInsertEventTap, kCGEventTapOptionDefault,
        mask, callback, None,
    )
    if tap is None:
        print("")
        print("无法监听键盘:还缺少「输入监控」权限。")
        print("请打开 系统设置 → 隐私与安全性 → 输入监控,")
        print("把你运行本程序的「终端」(或 iTerm)打开,然后重新运行。")
        preflight = getattr(Quartz, "CGRequestListenEventAccess", None)
        if preflight is not None:
            preflight()
        return 2

    tap_holder["tap"] = tap
    source = CFMachPortCreateRunLoopSource(None, tap, 0)
    run_loop = CFRunLoopGetCurrent()
    CFRunLoopAddSource(run_loop, source, kCFRunLoopCommonModes)
    CGEventTapEnable(tap, True)

    # 看门狗:长按中切走了窗口,也自动恢复正常速度
    stop_flag = threading.Event()

    def watchdog():
        while not stop_flag.is_set():
            if handler.is_right_down() and not vlc_is_frontmost():
                handler.release_right_silently()
            stop_flag.wait(0.2)

    threading.Thread(target=watchdog, daemon=True).start()

    def restore_on_exit():
        stop_flag.set()
        if handler.is_boosting() or handler.is_right_down():
            handler.release_right_silently()

    atexit.register(restore_on_exit)

    print("")
    print("VLC Booster 正在运行(保持这个窗口开着即可)")
    print("  单击 → :快进 %d 秒      单击 ← :快退 %d 秒" % (args.seek, args.seek))
    print("  长按 → :%g 倍速播放,松开恢复原速" % args.rate)
    print("  空格 / 点击画面:暂停 / 播放(VLC 自带)")
    print("  退出:按 Ctrl + C,或直接关闭本窗口")
    print("", flush=True)

    try:
        CFRunLoopRun()
    except KeyboardInterrupt:
        pass
    finally:
        CFRunLoopStop(run_loop)
    return 0


# ---------------------------------------------------------------- 入口

def ensure_vlc(args):
    """确认能连上 VLC 的遥控接口;连不上则帮忙启动 VLC。"""
    vlc = VLCRemote(host=args.host, port=args.port, password=args.password)
    if vlc.available():
        return vlc

    if vlc_is_running():
        print("")
        print("检测到 VLC 已经打开,但它的遥控接口没有开启,无法控制。")
        print("请先在 VLC 里按 Cmd + Q 完全退出,然后重新运行本程序,")
        print("我会用正确的方式帮你启动 VLC。")
        print("(也可以按 README 里的方法,在 VLC 设置里手动开启 Web 接口)")
        return None

    print("正在为你启动 VLC …", flush=True)
    try:
        launch_vlc(args.port, args.password)
    except Exception as exc:
        print("启动 VLC 失败:%s" % exc)
        print("请确认已经安装了 VLC(https://www.videolan.org/vlc/)。")
        return None

    for _ in range(30):
        if vlc.available():
            return vlc
        time.sleep(0.5)

    print("VLC 启动后仍然连不上它的遥控接口,请查看 README 的「常见问题」。")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="VLC 按键增强:长按→键 3 倍速,单击→/← 键快进/快退。")
    parser.add_argument("--host", default="127.0.0.1", help="VLC 遥控地址")
    parser.add_argument("--port", type=int, default=8080, help="VLC 遥控端口")
    parser.add_argument("--password", default="vlcpassword", help="VLC 遥控密码")
    parser.add_argument("--rate", type=float, default=3.0, help="长按倍速(默认 3)")
    parser.add_argument("--seek", type=int, default=5, help="单击跳转秒数(默认 5)")
    parser.add_argument("--hold", type=int, default=300,
                        help="按住多少毫秒算长按(默认 300)")
    args = parser.parse_args(argv)

    if ensure_vlc(args) is None:
        return 1
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
