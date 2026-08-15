from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置，全部可用环境变量覆盖（见 .env.example）。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── 基础 ──────────────────────────────────────────────────────────
    log_level: str = "INFO"
    data_dir: Path = Path("/app/data")
    apk_dir: Path = Path("/app/apks")
    database_url: str = "sqlite:////app/data/app.db"

    # ── 镜像 ──────────────────────────────────────────────────────────
    gateway_image: str = "ldm/proxy-gateway:latest"
    vnc_image: str = "ldm/android-vnc:latest"
    redroid_image: str = "redroid/redroid:13.0.0_64only-latest"

    # ── 设备默认值 ────────────────────────────────────────────────────
    device_width: int = 720
    device_height: int = 1280
    device_dpi: int = 320
    redroid_gpu_mode: str = "guest"
    device_memory_mb: int = 0  # 0 = 不限制

    # ── 端口分配 ──────────────────────────────────────────────────────
    device_port_base: int = 21000
    device_port_max: int = 21999

    # ── docker ────────────────────────────────────────────────────────
    docker_network: str = "ldm_net"
    container_prefix: str = "ldm"

    # ── 采集 ──────────────────────────────────────────────────────────
    default_interval_seconds: int = 60
    max_concurrent_tasks: int = 8
    selectors_dir: str = ""
    ui_action_timeout: float = 12.0
    max_product_scrolls: int = 8

    # ── 录屏 ──────────────────────────────────────────────────────────
    record_segment_seconds: int = 170
    record_bitrate: int = 4_000_000
    record_keep_segments: bool = False

    # ── 网关 ──────────────────────────────────────────────────────────
    gateway_dns_upstream: str = "https://1.1.1.1/dns-query"
    gateway_kill_switch: bool = True

    # ── 派生路径 ──────────────────────────────────────────────────────
    @property
    def screenshots_dir(self) -> Path:
        return self.data_dir / "screenshots"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def dumps_dir(self) -> Path:
        return self.data_dir / "dumps"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.screenshots_dir, self.recordings_dir, self.dumps_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
