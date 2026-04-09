from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..bootstrap import harness_lab_services
from ..types import WorkerHeartbeatRequest, WorkerPollRequest, WorkerRegisterRequest

router = APIRouter(prefix="/api/workers", tags=["workers"])


@router.get("")
async def list_workers():
    return {"success": True, "data": [worker.model_dump() for worker in harness_lab_services.workers.list_workers()]}


@router.post("")
async def register_worker(request: WorkerRegisterRequest):
    worker = harness_lab_services.workers.register_worker(request)
    return {"success": True, "data": worker.model_dump()}


@router.post("/register")
async def register_worker_alias(request: WorkerRegisterRequest):
    worker = harness_lab_services.workers.register_worker(request)
    return {"success": True, "data": worker.model_dump()}


@router.get("/{worker_id}")
async def get_worker(worker_id: str):
    try:
        worker = harness_lab_services.workers.get_worker(worker_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    recent_leases = harness_lab_services.runtime.list_leases(worker_id=worker_id)[-5:]
    recent_lease_ids = {lease.lease_id for lease in recent_leases}
    recent_events = [
        event.model_dump()
        for event in harness_lab_services.runtime.list_events(limit=500)
        if event.payload.get("worker_id") == worker_id or event.payload.get("lease_id") in recent_lease_ids
    ][-10:]
    return {
        "success": True,
        "data": worker.model_dump(),
        "health_summary": harness_lab_services.runtime.get_worker_health_summary(worker_id).model_dump(),
        "recent_leases": [lease.model_dump() for lease in recent_leases],
        "recent_events": recent_events,
    }


@router.post("/{worker_id}/poll")
async def poll_worker(worker_id: str, request: WorkerPollRequest):
    try:
        response = harness_lab_services.runtime.poll_worker(worker_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": response.model_dump()}


@router.post("/{worker_id}/heartbeat")
async def heartbeat(worker_id: str, request: WorkerHeartbeatRequest):
    try:
        worker = harness_lab_services.workers.heartbeat(worker_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": worker.model_dump()}
