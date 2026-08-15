from fastapi import APIRouter

from . import data, devices, proxies, recordings, system, tasks

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(proxies.router)
api_router.include_router(devices.router)
api_router.include_router(tasks.router)
api_router.include_router(data.router)
api_router.include_router(recordings.router)

__all__ = ["api_router"]
