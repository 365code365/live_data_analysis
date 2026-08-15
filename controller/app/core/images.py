from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from docker.errors import APIError, ImageNotFound

from ..config import settings
from . import events
from .docker_manager import DockerError, get_docker

log = logging.getLogger(__name__)

# 可以直接从仓库拉的镜像 / 只能本地构建的镜像
PULLABLE = {"android"}
BUILD_HINTS = {
    "gateway": "make build-gateway",
    "vnc": "make build-vnc",
    "android": "make pull-android",
}


def image_ref(target: str) -> str:
    return {
        "gateway": settings.gateway_image,
        "vnc": settings.vnc_image,
        "android": settings.redroid_image,
    }[target]


def split_ref(ref: str) -> tuple[str, str]:
    """把 repo:tag 拆开，注意 registry:port/repo 这种情况。"""
    if "/" in ref:
        head, tail = ref.rsplit("/", 1)
        if ":" in tail:
            name, tag = tail.rsplit(":", 1)
            return f"{head}/{name}", tag
        return ref, "latest"
    if ":" in ref:
        name, tag = ref.rsplit(":", 1)
        return name, tag
    return ref, "latest"


class ImagePullManager:
    """在控制台里拉镜像，并把进度暴露给前端轮询。

    redroid 镜像 600MB+，命令行拉的时候看不到进度很难受，
    所以这里把 docker pull 的分层进度聚合成一个百分比。
    """

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ── 查询 ──────────────────────────────────────────────────────────
    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._jobs.items()}

    def status(self, target: str) -> Optional[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(target)
            return dict(job) if job else None

    def is_running(self, target: str) -> bool:
        job = self.status(target)
        return bool(job and job.get("state") == "pulling")

    # ── 拉取 ──────────────────────────────────────────────────────────
    def start(self, target: str) -> dict[str, Any]:
        if target not in BUILD_HINTS:
            raise DockerError(f"未知镜像目标: {target}")
        if target not in PULLABLE:
            raise DockerError(
                f"{target} 镜像是本项目自带 Dockerfile 的本地镜像，需要在宿主机执行： {BUILD_HINTS[target]}"
            )
        ref = image_ref(target)
        with self._lock:
            job = self._jobs.get(target)
            if job and job["state"] == "pulling":
                return dict(job)
            self._jobs[target] = {
                "target": target,
                "image": ref,
                "state": "pulling",
                "percent": 0.0,
                "message": "准备拉取…",
                "error": None,
                "started_at": time.time(),
                "finished_at": None,
                "downloaded_mb": 0.0,
                "total_mb": 0.0,
            }
        threading.Thread(target=self._run, args=(target, ref), name=f"pull-{target}", daemon=True).start()
        events.emit(f"开始拉取镜像 {ref}", source="images")
        return self.status(target) or {}

    def _update(self, target: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(target)
            if job:
                job.update(fields)

    def _run(self, target: str, ref: str) -> None:
        repo, tag = split_ref(ref)
        layers: dict[str, tuple[int, int]] = {}
        try:
            client = get_docker().client
            stream = client.api.pull(repo, tag=tag, stream=True, decode=True)
            last_push = 0.0
            for chunk in stream:
                if "error" in chunk:
                    raise APIError(chunk["error"])
                status = chunk.get("status") or ""
                layer_id = chunk.get("id")
                detail = chunk.get("progressDetail") or {}
                total = detail.get("total")
                current = detail.get("current")
                if layer_id and total:
                    layers[layer_id] = (int(current or 0), int(total))

                done = sum(c for c, _ in layers.values())
                allb = sum(t for _, t in layers.values())
                percent = round(done / allb * 100, 1) if allb else 0.0

                # 别把主线程刷爆，200ms 更新一次够了
                now = time.time()
                if now - last_push > 0.2 or status.startswith(("Status", "Digest")):
                    last_push = now
                    self._update(
                        target,
                        percent=percent,
                        message=f"{status}{f' [{layer_id}]' if layer_id else ''}",
                        downloaded_mb=round(done / 1048576, 1),
                        total_mb=round(allb / 1048576, 1),
                    )

            # 拉完确认一下本地确实有了
            client.images.get(ref)
            self._update(
                target,
                state="done",
                percent=100.0,
                message="拉取完成",
                finished_at=time.time(),
            )
            events.emit(f"镜像拉取完成 {ref}", source="images")
        except ImageNotFound:
            msg = f"仓库里找不到 {ref}，检查 REDROID_IMAGE 是否写错（arm64 用 *_64only-latest，x86_64 用 13.0.0-latest）"
            self._fail(target, msg)
        except (APIError, DockerError) as exc:
            self._fail(target, str(exc))
        except Exception as exc:  # pragma: no cover
            log.exception("拉取镜像失败")
            self._fail(target, str(exc))

    def _fail(self, target: str, message: str) -> None:
        self._update(
            target,
            state="failed",
            error=message[:800],
            message="拉取失败",
            finished_at=time.time(),
        )
        events.emit(f"镜像拉取失败: {message[:300]}", level="error", source="images")


puller = ImagePullManager()


def images_overview() -> list[dict[str, Any]]:
    """给控制台用的镜像清单：是否就绪、大小、怎么补齐、能不能直接拉。"""
    docker = get_docker()
    out: list[dict[str, Any]] = []
    labels = {"gateway": "网关镜像", "vnc": "VNC 镜像", "android": "安卓镜像"}
    for target in ("gateway", "vnc", "android"):
        ref = image_ref(target)
        info: dict[str, Any] = {
            "target": target,
            "label": labels[target],
            "image": ref,
            "ready": False,
            "size_mb": None,
            "pullable": target in PULLABLE,
            "hint": BUILD_HINTS[target],
            "job": puller.status(target),
        }
        try:
            img = docker.client.images.get(ref)
            info["ready"] = True
            info["size_mb"] = round((img.attrs.get("Size") or 0) / 1048576, 1)
        except ImageNotFound:
            pass
        except (APIError, DockerError) as exc:
            info["error"] = str(exc)
        out.append(info)
    return out
