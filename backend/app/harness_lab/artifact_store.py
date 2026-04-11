from __future__ import annotations

import hashlib
import mimetypes
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

from .settings import HarnessLabSettings
from .types import ArtifactRef, ArtifactStoreStatus
from .utils import ensure_parent, new_id, utc_now


def _content_type_for(filename: str, explicit: Optional[str], is_text: bool) -> str:
    if explicit:
        return explicit
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed
    return "text/plain; charset=utf-8" if is_text else "application/octet-stream"


def _artifact_ref(
    *,
    run_id: str,
    artifact_type: str,
    storage_backend: str,
    storage_key: str,
    content: bytes,
    content_type: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> ArtifactRef:
    relative_path = storage_key if storage_backend == "local" else metadata.get("relative_path", "") if metadata else ""
    return ArtifactRef(
        artifact_id=new_id("artifact"),
        run_id=run_id,
        artifact_type=artifact_type,
        storage_backend=storage_backend,
        storage_key=storage_key,
        relative_path=relative_path,
        content_type=content_type,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        metadata=metadata or {},
        created_at=utc_now(),
    )


class ArtifactStore(ABC):
    backend_name = "unknown"

    @abstractmethod
    def write_text(
        self,
        run_id: str,
        artifact_type: str,
        filename: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        content_type: Optional[str] = None,
    ) -> ArtifactRef:
        """Persist text content and return a stable artifact reference."""

    @abstractmethod
    def write_bytes(
        self,
        run_id: str,
        artifact_type: str,
        filename: str,
        content: bytes,
        metadata: Optional[Dict[str, Any]] = None,
        content_type: Optional[str] = None,
    ) -> ArtifactRef:
        """Persist bytes and return a stable artifact reference."""

    @abstractmethod
    def read_text(self, artifact: ArtifactRef) -> str:
        """Read a text artifact."""

    @abstractmethod
    def read_bytes(self, artifact: ArtifactRef) -> bytes:
        """Read a binary artifact."""

    @abstractmethod
    def exists(self, artifact: ArtifactRef) -> bool:
        """Return True if the referenced artifact exists."""

    @abstractmethod
    def resolve_locator(self, artifact: ArtifactRef) -> str:
        """Return a backend-specific locator string for diagnostics."""

    @abstractmethod
    def status(self) -> ArtifactStoreStatus:
        """Return readiness for the configured artifact backend."""


class LocalFilesystemArtifactStore(ArtifactStore):
    backend_name = "local"

    def __init__(self, artifact_root: Path | str) -> None:
        self.artifact_root = Path(artifact_root)
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    def write_text(
        self,
        run_id: str,
        artifact_type: str,
        filename: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        content_type: Optional[str] = None,
    ) -> ArtifactRef:
        return self.write_bytes(
            run_id=run_id,
            artifact_type=artifact_type,
            filename=filename,
            content=content.encode("utf-8"),
            metadata=metadata,
            content_type=_content_type_for(filename, content_type, is_text=True),
        )

    def write_bytes(
        self,
        run_id: str,
        artifact_type: str,
        filename: str,
        content: bytes,
        metadata: Optional[Dict[str, Any]] = None,
        content_type: Optional[str] = None,
    ) -> ArtifactRef:
        storage_key = str(Path(run_id) / artifact_type / filename)
        artifact = _artifact_ref(
            run_id=run_id,
            artifact_type=artifact_type,
            storage_backend=self.backend_name,
            storage_key=storage_key,
            content=content,
            content_type=_content_type_for(filename, content_type, is_text=False),
            metadata=metadata,
        )
        artifact.relative_path = storage_key
        absolute_path = self.artifact_root / storage_key
        ensure_parent(absolute_path)
        absolute_path.write_bytes(content)
        return artifact

    def read_text(self, artifact: ArtifactRef) -> str:
        return self.read_bytes(artifact).decode("utf-8")

    def read_bytes(self, artifact: ArtifactRef) -> bytes:
        return (self.artifact_root / artifact.storage_key).read_bytes()

    def exists(self, artifact: ArtifactRef) -> bool:
        return (self.artifact_root / artifact.storage_key).exists()

    def resolve_locator(self, artifact: ArtifactRef) -> str:
        return str(self.artifact_root / artifact.storage_key)

    def status(self) -> ArtifactStoreStatus:
        return ArtifactStoreStatus(
            backend=self.backend_name,
            ready=self.artifact_root.exists(),
            bucket_or_root=str(self.artifact_root),
            last_error=None,
        )


class S3CompatibleArtifactStore(ArtifactStore):
    backend_name = "s3"

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        endpoint_url: Optional[str] = None,
        region: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.endpoint_url = endpoint_url
        self.region = region
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self._client = client
        self._last_error: Optional[str] = None

    def _build_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore
        except ImportError as exc:  # noqa: F401
            self._last_error = "boto3 is not installed for S3 artifact backend."
            return None
        session = boto3.session.Session()
        self._client = session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=self.region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
        )
        return self._client

    def _key_for(self, run_id: str, artifact_type: str, filename: str) -> str:
        base = Path(run_id) / artifact_type / filename
        return f"{self.prefix}/{base.as_posix()}".strip("/")

    def write_text(
        self,
        run_id: str,
        artifact_type: str,
        filename: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        content_type: Optional[str] = None,
    ) -> ArtifactRef:
        return self.write_bytes(
            run_id=run_id,
            artifact_type=artifact_type,
            filename=filename,
            content=content.encode("utf-8"),
            metadata=metadata,
            content_type=_content_type_for(filename, content_type, is_text=True),
        )

    def write_bytes(
        self,
        run_id: str,
        artifact_type: str,
        filename: str,
        content: bytes,
        metadata: Optional[Dict[str, Any]] = None,
        content_type: Optional[str] = None,
    ) -> ArtifactRef:
        client = self._build_client()
        if client is None:
            raise RuntimeError(self._last_error or "S3 artifact backend is not available.")
        key = self._key_for(run_id, artifact_type, filename)
        client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=_content_type_for(filename, content_type, is_text=False),
        )
        return _artifact_ref(
            run_id=run_id,
            artifact_type=artifact_type,
            storage_backend=self.backend_name,
            storage_key=key,
            content=content,
            content_type=_content_type_for(filename, content_type, is_text=False),
            metadata=metadata,
        )

    def read_text(self, artifact: ArtifactRef) -> str:
        return self.read_bytes(artifact).decode("utf-8")

    def read_bytes(self, artifact: ArtifactRef) -> bytes:
        client = self._build_client()
        if client is None:
            raise RuntimeError(self._last_error or "S3 artifact backend is not available.")
        response = client.get_object(Bucket=self.bucket, Key=artifact.storage_key)
        body = response["Body"]
        return body.read() if hasattr(body, "read") else bytes(body)

    def exists(self, artifact: ArtifactRef) -> bool:
        client = self._build_client()
        if client is None:
            return False
        try:
            client.head_object(Bucket=self.bucket, Key=artifact.storage_key)
            return True
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            return False

    def resolve_locator(self, artifact: ArtifactRef) -> str:
        if self.endpoint_url:
            return f"{self.endpoint_url.rstrip('/')}/{self.bucket}/{artifact.storage_key}"
        return f"s3://{self.bucket}/{artifact.storage_key}"

    def status(self) -> ArtifactStoreStatus:
        client = self._build_client()
        ready = False
        last_error = self._last_error
        if client is not None:
            try:
                if hasattr(client, "head_bucket"):
                    client.head_bucket(Bucket=self.bucket)
                ready = True
                last_error = None
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
        return ArtifactStoreStatus(
            backend=self.backend_name,
            ready=ready,
            bucket_or_root=self.bucket,
            last_error=last_error,
        )


def create_artifact_store(
    settings: HarnessLabSettings,
    artifact_root_override: Optional[Path | str] = None,
    client: Any | None = None,
) -> ArtifactStore:
    if settings.artifact_backend == "s3":
        return S3CompatibleArtifactStore(
            bucket=settings.artifact_bucket,
            prefix=settings.artifact_prefix,
            endpoint_url=settings.aws_endpoint_url,
            region=settings.aws_region,
            access_key_id=settings.aws_access_key_id,
            secret_access_key=settings.aws_secret_access_key,
            client=client,
        )
    resolved_root = artifact_root_override or settings.resolved_artifact_root()
    return LocalFilesystemArtifactStore(Path(resolved_root))
