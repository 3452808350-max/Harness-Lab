from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..bootstrap import harness_lab_services
from ..types import LeaseCompletionRequest, LeaseFailureRequest, LeaseReleaseRequest, WorkerEventBatch, WorkerHeartbeatRequest

router = APIRouter(prefix="/api/leases", tags=["leases"])


@router.get("")
async def list_leases(run_id: str | None = None, worker_id: str | None = None, status: str | None = None):
    leases = harness_lab_services.runtime.list_leases(run_id=run_id, worker_id=worker_id, status=status)
    return {"success": True, "data": [lease.model_dump() for lease in leases]}


@router.post("/{lease_id}/heartbeat")
async def heartbeat_lease(lease_id: str, request: WorkerHeartbeatRequest):
    try:
        lease = harness_lab_services.runtime.heartbeat_lease(lease_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": lease.model_dump()}


@router.post("/{lease_id}/events")
async def submit_worker_events(lease_id: str, request: WorkerEventBatch):
    try:
        lease = harness_lab_services.runtime.submit_worker_events(lease_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": lease.model_dump()}


@router.post("/{lease_id}/complete")
async def complete_lease(lease_id: str, request: LeaseCompletionRequest):
    try:
        run = await harness_lab_services.runtime.complete_lease(lease_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": run.model_dump()}


@router.post("/{lease_id}/fail")
async def fail_lease(lease_id: str, request: LeaseFailureRequest):
    try:
        run = await harness_lab_services.runtime.fail_lease(lease_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": run.model_dump()}


@router.post("/{lease_id}/release")
async def release_lease(lease_id: str, request: LeaseReleaseRequest):
    try:
        run = await harness_lab_services.runtime.release_lease(lease_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": run.model_dump()}
