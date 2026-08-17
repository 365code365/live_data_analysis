from fastapi import APIRouter

from . import apps, billing, data, devices, proxies, recordings, specs, system, tasks

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(specs.router)
api_router.include_router(proxies.router)
api_router.include_router(devices.router)
api_router.include_router(apps.router)
api_router.include_router(tasks.router)
api_router.include_router(data.router)
api_router.include_router(recordings.router)
api_router.include_router(billing.router)

__all__ = ["api_router"]
