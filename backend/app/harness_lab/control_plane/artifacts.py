from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from ..bootstrap import harness_lab_services

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}")
async def get_artifact(artifact_id: str):
    try:
        artifact = harness_lab_services.database.get_artifact(artifact_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    locator = harness_lab_services.database.artifact_store.resolve_locator(artifact)
    return {
        "success": True,
        "data": {
            **artifact.model_dump(),
            "locator": locator,
        },
    }


@router.get("/{artifact_id}/content")
async def get_artifact_content(artifact_id: str):
    try:
        artifact = harness_lab_services.database.get_artifact(artifact_id)
        content = harness_lab_services.database.read_artifact_bytes(artifact_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RuntimeError, FileNotFoundError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=artifact.content_type or "application/octet-stream",
        headers={
            "X-Harness-Artifact-Id": artifact.artifact_id,
            "X-Harness-Artifact-Backend": artifact.storage_backend,
            "X-Harness-Artifact-Key": artifact.storage_key,
            "X-Harness-Artifact-Sha256": artifact.sha256,
        },
    )
