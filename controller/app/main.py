from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import api_router
from .config import settings
from .core import events, scheduler
from .core.android import ensure_adb_server
from .core.docker_manager import DockerError, get_docker
from .core.recorder import recorder
from .db import init_db

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
log = logging.getLogger("ldm")

WEB_DIR = Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    log.info("启动 live-data-analysis controller v%s", __version__)
    settings.ensure_dirs()
    init_db()
    ensure_adb_server()
    try:
        get_docker().ensure_network()
    except DockerError as exc:
        log.error("Docker 不可用，设备相关功能会失败: %s", exc)
    scheduler.start()
    events.emit(f"控制器已启动 v{__version__}")
    try:
        yield
    finally:
        log.info("正在退出：停止调度与录屏")
        scheduler.shutdown()
        recorder.stop_all()


app = FastAPI(
    title="Live Data Analysis",
    description="Docker + 安卓容器 + VNC 的抖音/小红书直播间与商品监控平台",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def _asset_version() -> str:
    """用 css/js 的 mtime 生成版本号，改完前端刷新页面即生效，不会吃到浏览器缓存。"""
    stamps = []
    for name in ("style.css", "app.js"):
        f = WEB_DIR / name
        stamps.append(str(int(f.stat().st_mtime)) if f.exists() else "0")
    return hashlib.md5("-".join(stamps).encode()).hexdigest()[:10]


@app.get("/", include_in_schema=False)
def index():  # noqa: ANN201
    index_file = WEB_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse({"message": "控制台未打包，请访问 /docs 使用 API"})
    html = index_file.read_text(encoding="utf-8").replace("__ASSET_V__", _asset_version())
    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():  # noqa: ANN201
    return JSONResponse(status_code=204, content=None)
