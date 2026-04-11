"""System-level types (events, artifacts, reports)."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    """Envelope for system events."""
    seq: int
    event_id: str
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ArtifactRef(BaseModel):
    """Reference to an artifact."""
    artifact_id: str
    run_id: Optional[str] = None
    artifact_type: str
    storage_backend: str = "local"
    storage_key: str = ""
    relative_path: str = ""
    content_type: str = "text/plain; charset=utf-8"
    size_bytes: int = 0
    sha256: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ArtifactStoreStatus(BaseModel):
    """Status of the configured artifact backend."""
    backend: str
    ready: bool
    bucket_or_root: str
    last_error: Optional[str] = None


class DoctorReport(BaseModel):
    """Health check report."""
    control_plane: Dict[str, Any] = Field(default_factory=dict)
    provider: Dict[str, Any] = Field(default_factory=dict)
    workers: Dict[str, Any] = Field(default_factory=dict)
    improvement_plane: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
