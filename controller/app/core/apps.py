from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml

from ..config import settings
from . import events
from .android import AndroidDevice, AndroidError, get_device

log = logging.getLogger(__name__)

BUILTIN_CATALOG = Path(__file__).resolve().parent.parent / "apps_catalog.yaml"
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


# ── 应用目录 ──────────────────────────────────────────────────────────────
def catalog_path() -> Path:
    override = getattr(settings, "apps_catalog_file", "") or ""
    if override:
        p = Path(override)
        if p.exists():
            return p
    return BUILTIN_CATALOG


def load_catalog() -> list[dict[str, Any]]:
    path = catalog_path()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.error("读取应用目录失败 %s: %s", path, exc)
        return []
    apps = data.get("apps") or []
    out = []
    for item in apps:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        out.append(
            {
                "key": str(item["key"]),
                "name": item.get("name") or item["key"],
                "package": item.get("package"),
                "category": item.get("category") or "其它",
                "url": (item.get("url") or "").strip(),
                "page": (item.get("page") or "").strip(),
                "note": (item.get("note") or "").strip(),
                "installable": bool((item.get("url") or "").strip()),
            }
        )
    return out


def catalog_entry(key: str) -> Optional[dict[str, Any]]:
    for item in load_catalog():
        if item["key"] == key:
            return item
    return None


# ── 本地 apk 文件 ─────────────────────────────────────────────────────────
def safe_filename(name: str) -> str:
    name = Path(name or "").name
    name = SAFE_NAME.sub("_", name).strip("._") or "upload.apk"
    if not name.lower().endswith(".apk"):
        name += ".apk"
    return name


def local_apks() -> list[dict[str, Any]]:
    settings.apk_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(settings.apk_dir.glob("*.apk")):
        st = p.stat()
        out.append(
            {
                "filename": p.name,
                "size_mb": round(st.st_size / 1048576, 1),
                "modified_at": int(st.st_mtime),
            }
        )
    return out


def resolve_local(filename: str) -> Path:
    """只允许命中 apks/ 目录内的文件，挡掉路径穿越。"""
    root = settings.apk_dir.resolve()
    target = (root / Path(filename).name).resolve()
    if not str(target).startswith(str(root)):
        raise AndroidError("路径越界")
    if not target.exists():
        raise AndroidError(f"apks/ 下找不到 {Path(filename).name}")
    return target


def delete_local(filename: str) -> None:
    resolve_local(filename).unlink(missing_ok=True)


# ── 安装任务（下载 + 安装，带进度）────────────────────────────────────────
class AppJobManager:
    """一台设备同一时刻只跑一个安装任务，进度给前端轮询。"""

    def __init__(self) -> None:
        self._jobs: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, device_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(device_id)
            return dict(job) if job else None

    def running(self, device_id: int) -> bool:
        job = self.get(device_id)
        return bool(job and job["state"] in {"downloading", "installing"})

    def _set(self, device_id: int, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(device_id)
            if job:
                job.update(fields)

    def start(
        self,
        *,
        device_id: int,
        addr: str,
        source: str,
        name: str,
        url: Optional[str] = None,
        filename: Optional[str] = None,
        keep_file: bool = True,
    ) -> dict[str, Any]:
        if self.running(device_id):
            raise AndroidError("该设备上已有安装任务在跑，等它结束再来")
        with self._lock:
            self._jobs[device_id] = {
                "device_id": device_id,
                "name": name,
                "source": source,
                "state": "downloading" if source == "url" else "installing",
                "percent": 0.0,
                "message": "准备中…",
                "error": None,
                "filename": filename,
                "package": None,
                "started_at": time.time(),
                "finished_at": None,
            }
        threading.Thread(
            target=self._run,
            args=(device_id, addr, source, url, filename, keep_file),
            name=f"appjob-{device_id}",
            daemon=True,
        ).start()
        return self.get(device_id) or {}

    def _run(
        self,
        device_id: int,
        addr: str,
        source: str,
        url: Optional[str],
        filename: Optional[str],
        keep_file: bool,
    ) -> None:
        path: Optional[Path] = None
        try:
            if source == "url":
                if not url:
                    raise AndroidError("缺少下载地址")
                path = self._download(device_id, url, filename)
            else:
                path = resolve_local(filename or "")

            self._set(device_id, state="installing", percent=100.0, message="正在安装到设备…")
            dev = get_device(addr)
            if not dev.is_booted():
                raise AndroidError("安卓还没启动完成，稍等 1-2 分钟再装")
            out = dev.install_apk(str(path))
            pkg = self._guess_package(dev, path)
            self._set(
                device_id,
                state="done",
                message=out.strip()[:200] or "安装完成",
                package=pkg,
                filename=path.name,
                finished_at=time.time(),
            )
            events.emit(f"已安装 {path.name}" + (f"（{pkg}）" if pkg else ""), source="apps", device_id=device_id)
        except Exception as exc:
            log.warning("安装任务失败 device=%s: %s", device_id, exc)
            self._set(device_id, state="failed", error=str(exc)[:600], message="失败", finished_at=time.time())
            events.emit(f"安装失败: {exc}", level="error", source="apps", device_id=device_id)
        finally:
            if path and not keep_file:
                path.unlink(missing_ok=True)

    def _download(self, device_id: int, url: str, filename: Optional[str]) -> Path:
        settings.apk_dir.mkdir(parents=True, exist_ok=True)
        self._set(device_id, message=f"下载中 {url[:80]}")
        with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as resp:
            resp.raise_for_status()
            name = filename or ""
            if not name:
                # 优先用响应头里的文件名，其次用最终 URL 的路径
                disp = resp.headers.get("content-disposition", "")
                m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disp)
                name = m.group(1) if m else Path(str(resp.url).split("?")[0]).name
            name = safe_filename(name)
            dest = settings.apk_dir / name
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            last = 0.0
            with dest.open("wb") as fp:
                for chunk in resp.iter_bytes(chunk_size=256 * 1024):
                    fp.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last > 0.3:
                        last = now
                        self._set(
                            device_id,
                            percent=round(done / total * 100, 1) if total else 0.0,
                            message=f"下载中 {done // 1048576}MB"
                            + (f" / {total // 1048576}MB" if total else ""),
                            filename=name,
                        )
        if dest.stat().st_size < 1024:
            dest.unlink(missing_ok=True)
            raise AndroidError("下载到的文件太小，可能不是 apk（检查地址是否需要登录或跳转到了网页）")
        return dest

    @staticmethod
    def _guess_package(dev: AndroidDevice, path: Path) -> Optional[str]:
        """装完之后找出包名：apk 文件名往往不含包名，用最近安装的第三方包反推。"""
        try:
            raw = dev.shell("pm list packages -3 -U", timeout=30)
            pkgs = re.findall(r"package:(\S+)", raw or "")
            return pkgs[-1] if pkgs else None
        except Exception:
            return None


app_jobs = AppJobManager()


# ── 已安装应用 ────────────────────────────────────────────────────────────
def installed_apps(addr: str) -> list[dict[str, Any]]:
    dev = get_device(addr)
    pkgs = dev.list_packages()
    known = {a["package"]: a["name"] for a in load_catalog() if a.get("package")}
    out = []
    for pkg in sorted(pkgs):
        out.append({"package": pkg, "name": known.get(pkg), "is_platform_app": pkg in known})
    return out


def uninstall_app(addr: str, package: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._]+", package or ""):
        raise AndroidError(f"包名不合法: {package}")
    return get_device(addr).uninstall(package)
