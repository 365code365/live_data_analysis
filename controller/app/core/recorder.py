from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import settings
from ..db import session_scope
from ..models import Recording, RecordingStatus, utcnow
from . import events
from .android import ensure_adb_server

log = logging.getLogger(__name__)

# screenrecord 单次最长 180s，留点余量分段，段与段之间用 ffmpeg 无损拼接
HARD_SEGMENT_LIMIT = 180


class RecordingSession(threading.Thread):
    """一路录屏。分段 screenrecord → 逐段 pull → 结束后 ffmpeg concat。"""

    def __init__(
        self,
        *,
        recording_id: int,
        device_id: int,
        addr: str,
        out_dir: Path,
        bitrate: int,
        segment_seconds: int,
        size: Optional[str] = None,
        max_duration_seconds: Optional[int] = None,
    ) -> None:
        super().__init__(name=f"rec-{recording_id}", daemon=True)
        self.recording_id = recording_id
        self.device_id = device_id
        self.addr = addr
        self.out_dir = out_dir
        self.bitrate = bitrate
        self.segment_seconds = min(max(10, segment_seconds), HARD_SEGMENT_LIMIT)
        self.size = size
        self.max_duration_seconds = max_duration_seconds

        # 注意别叫 _stop：threading.Thread 内部有同名方法，覆盖它会让 is_alive() 直接抛异常
        self._stop_event = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self.segments: list[Path] = []
        self.error: Optional[str] = None
        self.output: Optional[Path] = None
        self.started_at = time.time()

    # ── 控制 ──────────────────────────────────────────────────────────
    def request_stop(self) -> None:
        if self._stop_event.is_set():
            return
        log.info("录屏 %s 收到停止请求", self.recording_id)
        self._stop_event.set()
        self._interrupt_remote()

    def _interrupt_remote(self) -> None:
        """给设备上的 screenrecord 发 SIGINT，让它把 mp4 写完整（直接 kill 会得到坏文件）。"""
        try:
            self._adb(
                ["shell", "pid=$(pidof screenrecord); if [ -n \"$pid\" ]; then kill -2 $pid; fi"],
                timeout=20,
            )
        except Exception as exc:
            log.warning("中断远端 screenrecord 失败: %s", exc)

    def _adb(self, args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["adb", "-s", self.addr, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    # ── 主循环 ────────────────────────────────────────────────────────
    def run(self) -> None:
        ensure_adb_server()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["adb", "connect", self.addr], capture_output=True, text=True, timeout=30)

        idx = 0
        try:
            while not self._stop_event.is_set():
                if self.max_duration_seconds and (time.time() - self.started_at) >= self.max_duration_seconds:
                    log.info("录屏 %s 达到最长时长，自动结束", self.recording_id)
                    break
                idx += 1
                remote = f"/sdcard/ldm_rec_{self.recording_id}_{idx:04d}.mp4"
                local = self.out_dir / f"seg_{idx:04d}.mp4"
                if not self._record_segment(remote, local, idx):
                    break
        except Exception as exc:  # pragma: no cover
            self.error = str(exc)
            log.exception("录屏 %s 异常", self.recording_id)
        finally:
            self._finalize()

    def _record_segment(self, remote: str, local: Path, idx: int) -> bool:
        cmd = ["adb", "-s", self.addr, "shell", "screenrecord",
               "--bit-rate", str(self.bitrate),
               "--time-limit", str(self.segment_seconds)]
        if self.size:
            cmd += ["--size", self.size]
        cmd.append(remote)

        log.debug("录屏 %s 分段 %s 开始", self.recording_id, idx)
        with self._proc_lock:
            if self._stop_event.is_set():
                return False
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        proc = self._proc
        try:
            _, stderr = proc.communicate(timeout=self.segment_seconds + 90)
        except subprocess.TimeoutExpired:
            proc.kill()
            stderr = "screenrecord 超时"
        finally:
            with self._proc_lock:
                self._proc = None

        # SIGINT 结束时 screenrecord 也会返回非 0，但文件是完整的，所以先尝试拉取
        time.sleep(1.0)
        pulled = self._pull(remote, local)
        if pulled:
            self.segments.append(local)
            self._bump_segment_count(len(self.segments))
        else:
            msg = (stderr or "").strip()
            log.warning("录屏 %s 分段 %s 无输出: %s", self.recording_id, idx, msg[:200])
            low = msg.lower()
            # 设备掉线属于硬错误，别原地死循环
            if "not found" in low or "offline" in low or "closed" in low:
                self.error = msg[:500] or "设备连接中断"
                return False
            if not self._stop_event.is_set():
                time.sleep(3)

        return not self._stop_event.is_set()

    def _pull(self, remote: str, local: Path) -> bool:
        try:
            res = self._adb(["pull", remote, str(local)], timeout=300)
            if res.returncode != 0 or not local.exists() or local.stat().st_size < 1024:
                if local.exists() and local.stat().st_size < 1024:
                    local.unlink(missing_ok=True)
                return False
            return True
        except Exception as exc:
            log.warning("拉取录屏分段失败: %s", exc)
            return False
        finally:
            try:
                self._adb(["shell", "rm", "-f", remote], timeout=30)
            except Exception:
                pass

    # ── 收尾 ──────────────────────────────────────────────────────────
    def _finalize(self) -> None:
        self._set_status(RecordingStatus.merging)
        output: Optional[Path] = None
        if not self.segments:
            self.error = self.error or "没有录到任何有效分段"
        elif len(self.segments) == 1:
            output = self.out_dir / "output.mp4"
            try:
                shutil.copy2(self.segments[0], output)
            except Exception as exc:
                self.error = f"拷贝分段失败: {exc}"
                output = self.segments[0]
        else:
            output = self._merge()

        self.output = output
        duration = self._probe_duration(output) if output else None
        size = output.stat().st_size if output and output.exists() else None

        if output and not settings.record_keep_segments:
            for seg in self.segments:
                if seg != output:
                    seg.unlink(missing_ok=True)

        with session_scope() as session:
            rec = session.get(Recording, self.recording_id)
            if rec:
                rec.ended_at = utcnow()
                rec.file_path = str(output) if output else None
                rec.size_bytes = size
                rec.duration_seconds = duration if duration is not None else round(time.time() - self.started_at, 1)
                rec.segment_count = len(self.segments)
                rec.error = self.error
                rec.status = RecordingStatus.done if output and not self.error else RecordingStatus.failed
                session.add(rec)

        events.emit(
            f"录屏结束: {output.name if output else '无输出'}"
            + (f"（{len(self.segments)} 段, {round((size or 0)/1048576, 1)}MB）" if output else f"，错误: {self.error}"),
            level="info" if output else "error",
            source="recorder",
            device_id=self.device_id,
        )

    def _merge(self) -> Optional[Path]:
        output = self.out_dir / "output.mp4"
        list_file = self.out_dir / "segments.txt"
        list_file.write_text(
            "".join(f"file '{seg.name}'\n" for seg in self.segments),
            encoding="utf-8",
        )
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", list_file.name,
            "-c", "copy", "-movflags", "+faststart", output.name,
        ]
        try:
            res = subprocess.run(cmd, cwd=self.out_dir, capture_output=True, text=True, timeout=1800)
            if res.returncode != 0 or not output.exists():
                self.error = f"ffmpeg 合并失败: {res.stderr.strip()[:300]}"
                log.error(self.error)
                return self.segments[0]
            return output
        except Exception as exc:
            self.error = f"ffmpeg 执行异常: {exc}"
            return self.segments[0]
        finally:
            if not settings.record_keep_segments:
                list_file.unlink(missing_ok=True)

    @staticmethod
    def _probe_duration(path: Optional[Path]) -> Optional[float]:
        if not path or not path.exists():
            return None
        try:
            res = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", str(path)],
                capture_output=True, text=True, timeout=60,
            )
            data = json.loads(res.stdout or "{}")
            return round(float(data["format"]["duration"]), 2)
        except Exception:
            return None

    def _set_status(self, status: RecordingStatus) -> None:
        with session_scope() as session:
            rec = session.get(Recording, self.recording_id)
            if rec:
                rec.status = status
                session.add(rec)

    def _bump_segment_count(self, count: int) -> None:
        with session_scope() as session:
            rec = session.get(Recording, self.recording_id)
            if rec:
                rec.segment_count = count
                session.add(rec)


class RecorderManager:
    """按设备维度管理录屏，一台设备同一时刻只允许一路录屏。"""

    def __init__(self) -> None:
        self._sessions: dict[int, RecordingSession] = {}
        self._lock = threading.Lock()

    def is_recording(self, device_id: int) -> bool:
        with self._lock:
            s = self._sessions.get(device_id)
            return bool(s and s.is_alive())

    def active(self) -> dict[int, int]:
        """device_id → recording_id"""
        with self._lock:
            return {d: s.recording_id for d, s in self._sessions.items() if s.is_alive()}

    def start(
        self,
        *,
        device_id: int,
        addr: str,
        task_id: Optional[int] = None,
        bitrate: Optional[int] = None,
        segment_seconds: Optional[int] = None,
        size: Optional[str] = None,
        max_duration_seconds: Optional[int] = None,
    ) -> int:
        with self._lock:
            existing = self._sessions.get(device_id)
            if existing and existing.is_alive():
                return existing.recording_id

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            sub = f"task_{task_id}" if task_id else f"device_{device_id}"
            out_dir = settings.recordings_dir / sub / stamp

            with session_scope() as session:
                rec = Recording(
                    task_id=task_id,
                    device_id=device_id,
                    status=RecordingStatus.recording,
                    started_at=utcnow(),
                )
                session.add(rec)
                session.flush()
                recording_id = int(rec.id)

            sess = RecordingSession(
                recording_id=recording_id,
                device_id=device_id,
                addr=addr,
                out_dir=out_dir,
                bitrate=bitrate or settings.record_bitrate,
                segment_seconds=segment_seconds or settings.record_segment_seconds,
                size=size,
                max_duration_seconds=max_duration_seconds,
            )
            self._sessions[device_id] = sess
            sess.start()

        events.emit(f"开始录屏（recording={recording_id}）", source="recorder", device_id=device_id, task_id=task_id)
        return recording_id

    def stop(self, device_id: int, *, wait: bool = True, timeout: float = 120) -> Optional[int]:
        with self._lock:
            sess = self._sessions.get(device_id)
        if sess is None:
            return None
        sess.request_stop()
        if wait:
            sess.join(timeout=timeout)
        with self._lock:
            if self._sessions.get(device_id) is sess and not sess.is_alive():
                self._sessions.pop(device_id, None)
        return sess.recording_id

    def stop_all(self) -> None:
        for device_id in list(self.active()):
            try:
                self.stop(device_id, wait=True, timeout=60)
            except Exception:
                log.exception("停止录屏失败 device=%s", device_id)


recorder = RecorderManager()
