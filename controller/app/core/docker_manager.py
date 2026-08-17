from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

import docker
from docker.errors import APIError, ImageNotFound, NotFound

from ..config import settings

log = logging.getLogger(__name__)

ROLE_GATEWAY = "gw"
ROLE_ANDROID = "android"
ROLE_VNC = "vnc"


class DockerError(RuntimeError):
    pass


@dataclass
class StackNames:
    gw: str
    android: str
    vnc: str
    volume: str


class DockerManager:
    """把「一台安卓设备」翻译成三个容器：网关 + 安卓 + 画面。

    三者共享网关容器的 network namespace，所以：
      * 安卓的所有流量（含 DNS）都从网关的代理出去；
      * 画面容器直接 `adb connect 127.0.0.1:5555`；
      * 对外只需要网关容器发布端口。
    """

    def __init__(self) -> None:
        self._quota_supported: Optional[bool] = None
        try:
            self.client = docker.from_env()
            self.client.ping()
        except Exception as exc:  # pragma: no cover
            raise DockerError(
                f"无法连接 Docker（确认已挂载 /var/run/docker.sock）: {exc}"
            ) from exc

    # ── 命名 ──────────────────────────────────────────────────────────
    @staticmethod
    def names(device_id: int) -> StackNames:
        p = settings.container_prefix
        return StackNames(
            gw=f"{p}_gw_{device_id}",
            android=f"{p}_android_{device_id}",
            vnc=f"{p}_vnc_{device_id}",
            volume=f"{p}_android_data_{device_id}",
        )

    def _labels(self, device_id: int, role: str) -> dict[str, str]:
        return {
            "ldm.managed": "true",
            "ldm.device_id": str(device_id),
            "ldm.role": role,
        }

    # ── 基础设施 ──────────────────────────────────────────────────────
    def ensure_network(self) -> None:
        try:
            self.client.networks.get(settings.docker_network)
        except NotFound:
            log.info("创建 docker 网络 %s", settings.docker_network)
            self.client.networks.create(settings.docker_network, driver="bridge")

    def ensure_images(self) -> dict[str, bool]:
        """检查所需镜像是否本地存在（不自动拉取安卓大镜像，避免阻塞请求）。"""
        result = {}
        for key, image in (
            ("gateway", settings.gateway_image),
            ("vnc", settings.vnc_image),
            ("android", settings.redroid_image),
        ):
            try:
                self.client.images.get(image)
                result[key] = True
            except ImageNotFound:
                result[key] = False
        return result

    def _get(self, name: str):
        try:
            return self.client.containers.get(name)
        except NotFound:
            return None

    def _remove_if_exists(self, name: str) -> None:
        c = self._get(name)
        if c is not None:
            log.info("移除残留容器 %s", name)
            try:
                c.remove(force=True)
            except APIError as exc:
                log.warning("移除 %s 失败: %s", name, exc)

    # ── 启动 ──────────────────────────────────────────────────────────
    def start_stack(
        self,
        *,
        device_id: int,
        width: int,
        height: int,
        dpi: int,
        adb_port: int,
        novnc_port: int,
        proxy_url: Optional[str],
        audio_port: Optional[int] = None,
        android_image: Optional[str] = None,
        vnc_password: Optional[str] = None,
        enable_audio: bool = True,
        memory_mb: int = 0,
        cpu_limit: float = 0,
        disk_gb: int = 0,
    ) -> dict[str, Any]:
        self.ensure_network()
        n = self.names(device_id)
        image = android_image or settings.redroid_image

        # 幂等：先清掉同名残留（顺序：先依赖方，再网关）
        for name in (n.vnc, n.android, n.gw):
            self._remove_if_exists(name)

        quota_ok = self._ensure_volume(n.volume, device_id, disk_gb=disk_gb)

        gw = self._start_gateway(device_id, n, adb_port, novnc_port, audio_port, proxy_url)
        try:
            android = self._start_android(
                device_id, n, gw.id, image, width, height, dpi, memory_mb, cpu_limit
            )
            vnc = self._start_vnc(device_id, n, gw.id, width, height, vnc_password, enable_audio)
        except Exception:
            # 起一半失败就整组回收，避免留下半死状态
            for name in (n.vnc, n.android, n.gw):
                self._remove_if_exists(name)
            raise

        return {
            "gw": gw.name,
            "android": android.name,
            "vnc": vnc.name,
            "volume": n.volume,
            "disk_quota": quota_ok,
        }

    def _ensure_volume(self, name: str, device_id: int, *, disk_gb: int = 0) -> bool:
        """确保安卓 /data 卷存在。返回是否真的加上了磁盘配额。

        docker 的 local 卷驱动只有在宿主文件系统开了 project quota（xfs prjquota，
        或 ext4 + prjquota）时才支持 size 限额，否则 create 会直接报
        "quota size requested but no quota support"。这里尝试带配额创建，
        失败就退回普通卷并把结果如实报上去 —— 界面上会写「未限额」，不假装。
        """
        try:
            self.client.volumes.get(name)
            return False  # 已存在的卷不改动，配额情况以创建时为准
        except NotFound:
            pass

        labels = self._labels(device_id, "data")
        if disk_gb and disk_gb > 0:
            try:
                self.client.volumes.create(
                    name=name,
                    labels=labels,
                    driver_opts={"size": f"{int(disk_gb)}g"},
                )
                log.info("创建安卓数据卷 %s（配额 %sGB）", name, disk_gb)
                return True
            except APIError as exc:
                log.warning("卷 %s 加磁盘配额失败（%s），退回不限额", name, str(exc).strip()[:120])

        log.info("创建安卓数据卷 %s", name)
        self.client.volumes.create(name=name, labels=labels)
        return False

    def disk_quota_supported(self) -> bool:
        """探一次宿主是否支持卷磁盘配额，结果缓存（用于界面上如实标注）。"""
        if self._quota_supported is None:
            probe = f"{settings.container_prefix}_quota_probe"
            self._remove_if_exists_volume(probe)
            try:
                self.client.volumes.create(name=probe, driver_opts={"size": "1g"})
                self._quota_supported = True
            except APIError as exc:
                log.info("宿主不支持卷磁盘配额: %s", str(exc).strip()[:120])
                self._quota_supported = False
            finally:
                self._remove_if_exists_volume(probe)
        return self._quota_supported

    def _remove_if_exists_volume(self, name: str) -> None:
        try:
            self.client.volumes.get(name).remove(force=True)
        except (NotFound, APIError):
            pass

    def _start_gateway(
        self,
        device_id: int,
        n: StackNames,
        adb_port: int,
        novnc_port: int,
        audio_port: Optional[int],
        proxy_url: Optional[str],
    ):
        env = {
            "PROXY_URL": proxy_url or "",
            "DNS_UPSTREAM": settings.gateway_dns_upstream,
            "DNS_FALLBACK": settings.gateway_dns_fallback,
            "KILL_SWITCH": "true" if (settings.gateway_kill_switch and proxy_url) else "false",
        }
        # 三个对外端口都发布在网关容器上（安卓/画面容器共享它的 netns）
        ports: dict[str, int] = {"5555/tcp": adb_port, "6080/tcp": novnc_port}
        if audio_port:
            ports["6081/tcp"] = audio_port

        log.info("启动网关 %s proxy=%s", n.gw, _mask(proxy_url))
        return self.client.containers.run(
            settings.gateway_image,
            name=n.gw,
            detach=True,
            environment=env,
            cap_add=["NET_ADMIN"],
            devices=["/dev/net/tun:/dev/net/tun:rwm"],
            sysctls={
                "net.ipv4.conf.all.src_valid_mark": "1",
                # 在内核层面关掉 IPv6，比靠 ip6tables 拦更彻底，
                # 也避免组件优先走 ::1 时被防火墙规则拖住
                "net.ipv6.conf.all.disable_ipv6": "1",
            },
            ports=ports,
            network=settings.docker_network,
            labels=self._labels(device_id, ROLE_GATEWAY),
            restart_policy={"Name": "unless-stopped"},
            hostname=f"gw{device_id}",
        )

    def _start_android(
        self,
        device_id: int,
        n: StackNames,
        gw_id: str,
        image: str,
        width: int,
        height: int,
        dpi: int,
        memory_mb: int = 0,
        cpu_limit: float = 0,
    ):
        command = [
            f"androidboot.redroid_width={width}",
            f"androidboot.redroid_height={height}",
            f"androidboot.redroid_dpi={dpi}",
            f"androidboot.redroid_gpu_mode={settings.redroid_gpu_mode}",
            "androidboot.redroid_fps=30",
            "androidboot.use_memfd=true",
        ]
        kwargs: dict[str, Any] = {}
        mem = memory_mb if memory_mb and memory_mb > 0 else settings.device_memory_mb
        if mem and mem > 0:
            kwargs["mem_limit"] = f"{int(mem)}m"
        if cpu_limit and cpu_limit > 0:
            # docker 用 cpu_quota/cpu_period 表达「几个核」
            kwargs["cpu_period"] = 100000
            kwargs["cpu_quota"] = int(float(cpu_limit) * 100000)

        log.info("启动安卓 %s image=%s %sx%s@%s", n.android, image, width, height, dpi)
        return self.client.containers.run(
            image,
            name=n.android,
            detach=True,
            privileged=True,
            network_mode=f"container:{gw_id}",
            volumes={n.volume: {"bind": "/data", "mode": "rw"}},
            command=command,
            labels=self._labels(device_id, ROLE_ANDROID),
            # 用 on-failure 限次而不是 unless-stopped：内核不支持时 redroid 会秒退，
            # 无限重启只会刷屏，限次后停下来更容易定位。
            restart_policy={"Name": "on-failure", "MaximumRetryCount": 5},
            **kwargs,
        )

    def _start_vnc(
        self,
        device_id: int,
        n: StackNames,
        gw_id: str,
        width: int,
        height: int,
        vnc_password: Optional[str],
        enable_audio: bool = True,
    ):
        env = {
            "ADB_TARGET": "127.0.0.1:5555",
            "SCREEN_WIDTH": str(width),
            "SCREEN_HEIGHT": str(height),
            "VNC_PASSWORD": vnc_password or "",
            "ENABLE_AUDIO": "true" if enable_audio else "false",
            "AUDIO_PORT": "6081",
        }
        log.info("启动画面容器 %s", n.vnc)
        return self.client.containers.run(
            settings.vnc_image,
            name=n.vnc,
            detach=True,
            environment=env,
            network_mode=f"container:{gw_id}",
            labels=self._labels(device_id, ROLE_VNC),
            restart_policy={"Name": "unless-stopped"},
            shm_size="256m",
        )

    # ── 停止 / 删除 ───────────────────────────────────────────────────
    def stop_stack(self, device_id: int) -> None:
        n = self.names(device_id)
        for name in (n.vnc, n.android, n.gw):
            c = self._get(name)
            if c is None:
                continue
            log.info("停止容器 %s", name)
            try:
                c.stop(timeout=15)
            except APIError as exc:
                log.warning("停止 %s 失败: %s", name, exc)

    def remove_stack(self, device_id: int, *, purge_data: bool = False) -> None:
        n = self.names(device_id)
        for name in (n.vnc, n.android, n.gw):
            self._remove_if_exists(name)
        if purge_data:
            try:
                self.client.volumes.get(n.volume).remove(force=True)
                log.info("已删除数据卷 %s", n.volume)
            except NotFound:
                pass
            except APIError as exc:
                log.warning("删除数据卷 %s 失败: %s", n.volume, exc)

    def restart_role(self, device_id: int, role: str) -> None:
        n = self.names(device_id)
        name = {ROLE_GATEWAY: n.gw, ROLE_ANDROID: n.android, ROLE_VNC: n.vnc}[role]
        c = self._get(name)
        if c is None:
            raise DockerError(f"容器不存在: {name}")
        c.restart(timeout=15)

    # ── 观测 ──────────────────────────────────────────────────────────
    def stack_status(self, device_id: int) -> dict[str, Optional[str]]:
        n = self.names(device_id)
        out: dict[str, Optional[str]] = {}
        for role, name in ((ROLE_GATEWAY, n.gw), (ROLE_ANDROID, n.android), (ROLE_VNC, n.vnc)):
            c = self._get(name)
            out[role] = c.status if c else None
        return out

    def logs(self, container_name: str, tail: int = 200) -> str:
        c = self._get(container_name)
        if c is None:
            return f"容器不存在: {container_name}"
        try:
            return c.logs(tail=tail, timestamps=False).decode("utf-8", "replace")
        except APIError as exc:
            return f"读取日志失败: {exc}"

    def exec_in_gateway(self, device_id: int, cmd: list[str], timeout: int = 30) -> tuple[int, str]:
        name = self.names(device_id).gw
        c = self._get(name)
        if c is None:
            raise DockerError(f"网关容器不存在: {name}")
        code, output = c.exec_run(cmd, demux=False)
        return code, output.decode("utf-8", "replace") if output else ""

    def egress_ip(self, device_id: int) -> dict[str, Any]:
        """在网关容器里查出口 IP，用来验证代理是否真的生效。"""
        code, out = self.exec_in_gateway(device_id, ["/usr/local/bin/egress-ip"])
        out = out.strip()
        if code != 0:
            return {"error": out or "egress-ip 执行失败"}
        try:
            return json.loads(out.splitlines()[-1])
        except json.JSONDecodeError:
            return {"error": f"无法解析输出: {out[:200]}"}

    def probe_proxy(self, proxy_url: str, *, timeout: int = 25) -> dict[str, Any]:
        """用一次性网关容器验证代理可用性（不影响已有设备）。"""
        self.ensure_network()
        name = f"{settings.container_prefix}_proxytest_{abs(hash(proxy_url)) % 100000}"
        self._remove_if_exists(name)
        container = self.client.containers.run(
            settings.gateway_image,
            name=name,
            detach=True,
            environment={
                "PROXY_URL": proxy_url,
                "DNS_UPSTREAM": settings.gateway_dns_upstream,
                "DNS_FALLBACK": settings.gateway_dns_fallback,
                "KILL_SWITCH": "false",
            },
            cap_add=["NET_ADMIN"],
            devices=["/dev/net/tun:/dev/net/tun:rwm"],
            network=settings.docker_network,
            labels={"ldm.managed": "true", "ldm.role": "proxytest"},
        )
        try:
            code, out = container.exec_run(
                ["/bin/bash", "-c", f"for i in $(seq 1 {timeout // 3}); do /usr/local/bin/egress-ip && exit 0; sleep 3; done; exit 1"],
            )
            text = (out or b"").decode("utf-8", "replace").strip()
            if code != 0:
                logs = container.logs(tail=40).decode("utf-8", "replace")
                return {"ok": False, "error": text or "代理不可用", "gateway_log": logs}
            try:
                data = json.loads(text.splitlines()[-1])
            except json.JSONDecodeError:
                return {"ok": False, "error": f"无法解析出口信息: {text[:200]}"}
            return {"ok": True, **data}
        finally:
            try:
                container.remove(force=True)
            except APIError:
                pass

    def list_managed(self) -> list[dict[str, Any]]:
        out = []
        for c in self.client.containers.list(all=True, filters={"label": "ldm.managed=true"}):
            out.append(
                {
                    "name": c.name,
                    "status": c.status,
                    "role": c.labels.get("ldm.role"),
                    "device_id": c.labels.get("ldm.device_id"),
                    "image": (c.image.tags or ["<none>"])[0] if c.image else "<none>",
                }
            )
        return out

    def used_host_ports(self) -> set[int]:
        """已被任何容器占用的宿主端口，端口分配时避让。"""
        used: set[int] = set()
        for c in self.client.containers.list(all=True):
            bindings = (c.attrs.get("HostConfig") or {}).get("PortBindings") or {}
            for maps in bindings.values():
                for m in maps or []:
                    hp = m.get("HostPort")
                    if hp and hp.isdigit():
                        used.add(int(hp))
        return used


def _mask(url: Optional[str]) -> str:
    if not url:
        return "direct"
    if "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    return f"{scheme}://***@{rest.split('@', 1)[1]}"


_manager: Optional[DockerManager] = None


def get_docker() -> DockerManager:
    global _manager
    if _manager is None:
        _manager = DockerManager()
    return _manager


__all__ = ["DockerManager", "DockerError", "get_docker"]
