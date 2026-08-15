from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

import adbutils

from ..config import settings

log = logging.getLogger(__name__)

_adb_server_lock = threading.Lock()
_adb_server_started = False


def ensure_adb_server() -> None:
    """控制器容器里需要一个本地 adb server。"""
    global _adb_server_started
    with _adb_server_lock:
        if _adb_server_started:
            return
        # adb 34 的 mDNS 自动发现在容器网络里可能把 start-server 挂死
        os.environ.setdefault("ADB_MDNS", "0")
        os.environ.setdefault("ADB_MDNS_AUTO_CONNECT", "0")
        os.environ.setdefault("ADB_MDNS_OPENSCREEN", "0")
        try:
            subprocess.run(
                ["adb", "start-server"],
                check=False,
                capture_output=True,
                timeout=30,
            )
            _adb_server_started = True
            log.info("adb server 已启动")
        except Exception as exc:  # pragma: no cover
            log.warning("adb start-server 失败: %s", exc)


class AndroidError(RuntimeError):
    pass


class AndroidDevice:
    """对一台安卓实例的操作封装（adb + uiautomator）。

    统一用 `adb connect <网关容器名>:5555`，因为设备容器共享网关 netns。
    """

    def __init__(self, addr: str) -> None:
        ensure_adb_server()
        self.addr = addr
        self._client = adbutils.AdbClient(host="127.0.0.1", port=5037)
        self._u2: Any = None
        self._lock = threading.RLock()

    # ── 连接 ──────────────────────────────────────────────────────────
    def connect(self, timeout: float = 15.0) -> None:
        try:
            self._client.connect(self.addr, timeout=timeout)
        except Exception as exc:
            log.debug("adb connect %s: %s", self.addr, exc)

    def disconnect(self) -> None:
        try:
            self._client.disconnect(self.addr)
        except Exception:
            pass

    @property
    def dev(self) -> adbutils.AdbDevice:
        self.connect()
        return self._client.device(self.addr)

    def state(self) -> str:
        try:
            self.connect()
            return self._client.device(self.addr).get_state() or "offline"
        except Exception:
            return "offline"

    def is_online(self) -> bool:
        return self.state() == "device"

    def is_booted(self) -> bool:
        try:
            return self.shell("getprop sys.boot_completed").strip() == "1"
        except Exception:
            return False

    def wait_boot(self, timeout: float = 300.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_booted():
                return True
            time.sleep(3)
        return False

    # ── 基础命令 ──────────────────────────────────────────────────────
    def shell(self, cmd: str | list[str], timeout: float = 30.0) -> str:
        with self._lock:
            out = self.dev.shell(cmd, timeout=timeout)
        return out if isinstance(out, str) else str(out)

    def prop(self, key: str) -> str:
        return self.shell(f"getprop {shlex.quote(key)}").strip()

    def device_info(self) -> dict[str, Any]:
        try:
            size = self.shell("wm size").strip()
            density = self.shell("wm density").strip()
            return {
                "state": self.state(),
                "booted": self.is_booted(),
                "android_version": self.prop("ro.build.version.release"),
                "sdk": self.prop("ro.build.version.sdk"),
                "model": self.prop("ro.product.model"),
                "size": size,
                "density": density,
                "current_app": self.current_package(),
            }
        except Exception as exc:
            return {"state": self.state(), "error": str(exc)}

    # ── 应用 ──────────────────────────────────────────────────────────
    def list_packages(self, keyword: str = "") -> list[str]:
        raw = self.shell("pm list packages -3")
        pkgs = [line.replace("package:", "").strip() for line in raw.splitlines() if line.strip()]
        return [p for p in pkgs if keyword in p] if keyword else pkgs

    def is_installed(self, package: str) -> bool:
        return bool(self.shell(f"pm path {shlex.quote(package)}").strip())

    def current_package(self) -> Optional[str]:
        raw = self.shell("dumpsys window 2>/dev/null | grep -E 'mCurrentFocus|mFocusedApp' | head -2")
        m = re.search(r"([A-Za-z0-9_.]+)/[A-Za-z0-9_.$]+", raw or "")
        return m.group(1) if m else None

    def start_app(self, package: str, *, stop_first: bool = False) -> None:
        if stop_first:
            self.stop_app(package)
        self.shell(f"monkey -p {shlex.quote(package)} -c android.intent.category.LAUNCHER 1", timeout=30)

    def stop_app(self, package: str) -> None:
        self.shell(f"am force-stop {shlex.quote(package)}")

    def open_deeplink(self, uri: str, package: Optional[str] = None) -> str:
        cmd = f"am start -a android.intent.action.VIEW -d {shlex.quote(uri)}"
        if package:
            cmd += f" {shlex.quote(package)}"
        return self.shell(cmd, timeout=30)

    def install_apk(self, apk_path: str | Path, *, reinstall: bool = True) -> str:
        apk_path = Path(apk_path)
        if not apk_path.exists():
            raise AndroidError(f"APK 不存在: {apk_path}")
        remote = f"/data/local/tmp/{apk_path.name}"
        with self._lock:
            dev = self.dev
            dev.sync.push(str(apk_path), remote)
            flags = "-r -g" if reinstall else "-g"
            out = dev.shell(f"pm install {flags} {shlex.quote(remote)}", timeout=600)
            dev.shell(f"rm -f {shlex.quote(remote)}")
        if "Success" not in (out or ""):
            raise AndroidError(f"安装失败: {out}")
        return out

    def uninstall(self, package: str) -> str:
        return self.shell(f"pm uninstall {shlex.quote(package)}", timeout=120)

    # ── 输入 ──────────────────────────────────────────────────────────
    def tap(self, x: int, y: int) -> None:
        self.shell(f"input tap {int(x)} {int(y)}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400) -> None:
        self.shell(f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}")

    def key(self, keycode: str | int) -> None:
        self.shell(f"input keyevent {keycode}")

    def back(self) -> None:
        self.key("KEYCODE_BACK")

    def home(self) -> None:
        self.key("KEYCODE_HOME")

    def input_text(self, text: str) -> None:
        self.shell(f"input text {shlex.quote(text)}")

    def wake(self) -> None:
        self.shell("input keyevent KEYCODE_WAKEUP")
        self.shell("svc power stayon true")

    def screen_size(self) -> tuple[int, int]:
        raw = self.shell("wm size")
        m = re.search(r"(\d+)x(\d+)", raw or "")
        if m:
            return int(m.group(1)), int(m.group(2))
        return settings.device_width, settings.device_height

    # ── 截图 / 控件树 ─────────────────────────────────────────────────
    def screenshot(self, dest: str | Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            try:
                img = self.dev.screenshot()
                img.save(dest)
                return dest
            except Exception as exc:
                log.debug("adbutils screenshot 失败，回落 screencap: %s", exc)
            remote = "/sdcard/_ldm_shot.png"
            dev = self.dev
            dev.shell(f"screencap -p {remote}", timeout=60)
            dev.sync.pull(remote, str(dest))
            dev.shell(f"rm -f {remote}")
        return dest

    @property
    def u2(self) -> Any:
        """uiautomator2 会话（懒加载，失败时返回 None 由调用方回落）。"""
        if self._u2 is not None:
            return self._u2
        try:
            import uiautomator2 as u2mod

            self.connect()
            self._u2 = u2mod.connect(self.addr)
            log.info("uiautomator2 已连接 %s", self.addr)
        except Exception as exc:
            log.warning("uiautomator2 连接失败(%s)，将使用 adb uiautomator dump 回落: %s", self.addr, exc)
            self._u2 = None
        return self._u2

    def dump_hierarchy(self, *, compressed: bool = False) -> str:
        """拿当前界面的控件树 XML。优先 uiautomator2，失败回落 adb dump。"""
        u2dev = self.u2
        if u2dev is not None:
            try:
                xml = u2dev.dump_hierarchy(compressed=compressed)
                if xml and "<hierarchy" in xml:
                    return xml
            except Exception as exc:
                log.warning("u2 dump_hierarchy 失败，回落 adb: %s", exc)
                self._u2 = None
        return self._dump_via_adb()

    def _dump_via_adb(self) -> str:
        remote = "/sdcard/_ldm_dump.xml"
        last_err = ""
        for attempt in range(3):
            out = self.shell(f"uiautomator dump --compressed {remote}", timeout=60)
            if "ERROR" in (out or "").upper() or "dumped" not in (out or "").lower():
                last_err = out or ""
                time.sleep(1.5)
                continue
            xml = self.shell(f"cat {remote}", timeout=60)
            self.shell(f"rm -f {remote}")
            if "<hierarchy" in xml:
                return xml
            last_err = xml[:200]
            time.sleep(1.0)
        raise AndroidError(f"无法获取控件树: {last_err[:300]}")


# ── 设备会话缓存 ──────────────────────────────────────────────────────
_devices: dict[str, AndroidDevice] = {}
_devices_lock = threading.Lock()


def get_device(addr: str) -> AndroidDevice:
    with _devices_lock:
        dev = _devices.get(addr)
        if dev is None:
            dev = AndroidDevice(addr)
            _devices[addr] = dev
        return dev


def drop_device(addr: str) -> None:
    with _devices_lock:
        dev = _devices.pop(addr, None)
    if dev is not None:
        dev.disconnect()
