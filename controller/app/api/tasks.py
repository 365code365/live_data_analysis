from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, func, select

from ..core import scheduler
from ..core.collector import run_task
from ..db import get_session
from ..models import Device, LiveSnapshot, MonitorTask
from ..schemas import Ok, TaskCreate, TaskUpdate

log = logging.getLogger(__name__)
router = APIRouter(prefix="/tasks", tags=["tasks"])


def _get(session: Session, task_id: int) -> MonitorTask:
    task = session.get(MonitorTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return task


def _out(session: Session, task: MonitorTask) -> dict[str, Any]:
    device = session.get(Device, task.device_id) if task.device_id else None
    snap_count = session.exec(
        select(func.count(LiveSnapshot.id)).where(LiveSnapshot.task_id == task.id)
    ).one()
    job = scheduler.get_scheduler().get_job(f"task_{task.id}") if task.id else None
    return {
        "id": task.id,
        "name": task.name,
        "platform": task.platform,
        "target": task.target,
        "device_id": task.device_id,
        "device_name": device.name if device else None,
        "device_status": device.status if device else None,
        "interval_seconds": task.interval_seconds,
        "enabled": task.enabled,
        "collect_products": task.collect_products,
        "collect_comments": task.collect_comments,
        "record_video": task.record_video,
        "keep_screenshot": task.keep_screenshot,
        "last_run_at": task.last_run_at,
        "next_run_at": job.next_run_time.isoformat() if job and job.next_run_time else task.next_run_at,
        "last_status": task.last_status,
        "last_error": task.last_error,
        "run_count": task.run_count,
        "fail_count": task.fail_count,
        "snapshot_count": snap_count,
        "scheduled": job is not None,
        "created_at": task.created_at,
    }


@router.get("")
def list_tasks(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    tasks = session.exec(select(MonitorTask).order_by(MonitorTask.id)).all()
    return [_out(session, t) for t in tasks]


@router.post("", status_code=201)
def create_task(payload: TaskCreate, session: Session = Depends(get_session)) -> dict[str, Any]:
    if payload.device_id and session.get(Device, payload.device_id) is None:
        raise HTTPException(400, "指定的设备不存在")
    task = MonitorTask(**payload.model_dump())
    session.add(task)
    session.commit()
    session.refresh(task)
    scheduler.sync_task(task)
    return _out(session, task)


@router.get("/jobs")
def list_jobs() -> dict[str, Any]:
    return {"jobs": scheduler.jobs()}


@router.post("/reload")
def reload_jobs() -> Ok:
    count = scheduler.reload_jobs()
    return Ok(message=f"已重载 {count} 个任务")


@router.get("/{task_id}")
def get_task(task_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    return _out(session, _get(session, task_id))


@router.patch("/{task_id}")
def update_task(task_id: int, payload: TaskUpdate, session: Session = Depends(get_session)) -> dict[str, Any]:
    task = _get(session, task_id)
    changes = payload.model_dump(exclude_unset=True)
    if "device_id" in changes and changes["device_id"] and session.get(Device, changes["device_id"]) is None:
        raise HTTPException(400, "指定的设备不存在")
    for key, value in changes.items():
        setattr(task, key, value)
    session.add(task)
    session.commit()
    session.refresh(task)
    scheduler.sync_task(task)
    return _out(session, task)


@router.delete("/{task_id}")
def delete_task(task_id: int, session: Session = Depends(get_session)) -> Ok:
    task = _get(session, task_id)
    scheduler.remove_task(task_id)
    session.delete(task)
    session.commit()
    return Ok(message="已删除")


@router.post("/{task_id}/run")
def run_now(
    task_id: int,
    background: BackgroundTasks,
    wait: bool = False,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """立即执行一次。wait=true 会同步等结果（调试选择器时很方便，但可能几十秒）。"""
    _get(session, task_id)
    if wait:
        return run_task(task_id)
    background.add_task(run_task, task_id)
    return {"ok": True, "message": "已提交后台执行，稍后在数据页查看结果"}
