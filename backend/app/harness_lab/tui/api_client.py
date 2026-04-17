"""
Harness Lab TUI API Client.

Provides async HTTP client for Control Plane API communication.
Features:
- Connection pooling with httpx.AsyncClient
- Automatic retry with exponential backoff
- Graceful error handling
- Optional state caching
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx


@dataclass
class APIConfig:
    """Configuration for Control Plane API client.
    
    Attributes:
        base_url: Base URL for the Control Plane API (default: localhost:4600)
        timeout: Request timeout in seconds
        retry_count: Number of retry attempts for failed requests
        retry_delay: Base delay for exponential backoff (seconds)
        cache_ttl: Cache time-to-live in seconds (0 = no caching)
    """
    base_url: str = "http://localhost:4600"
    timeout: float = 5.0
    retry_count: int = 3
    retry_delay: float = 1.0
    cache_ttl: float = 0.0  # 0 = no caching


@dataclass
class CacheEntry:
    """Cached API response entry."""
    data: Any
    timestamp: float
    ttl: float
    
    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        if self.ttl <= 0:
            return True
        return time.time() - self.timestamp > self.ttl


class ControlPlaneClient:
    """Async HTTP client for Harness Lab Control Plane API.
    
    Provides connection pooling, retry logic, and error handling
    for TUI dashboard operations.
    
    Usage:
        client = ControlPlaneClient(APIConfig(base_url="http://localhost:4600"))
        await client.connect()
        
        # Get system health
        health = await client.get_health()
        
        # List workers
        workers = await client.list_workers()
        
        # Drain a worker
        result = await client.drain_worker("w-01", reason="Maintenance")
        
        await client.disconnect()
    
    Attributes:
        is_connected: Whether client has active connection to API
        last_error: Last error encountered during API call
    """
    
    def __init__(self, config: APIConfig = None):
        """Initialize API client with configuration.
        
        Args:
            config: API configuration (uses defaults if not provided)
        """
        self._config = config or APIConfig()
        self._client: Optional[httpx.AsyncClient] = None
        self._connected: bool = False
        self._last_error: Optional[str] = None
        self._cache: Dict[str, CacheEntry] = {}
    
    @property
    def is_connected(self) -> bool:
        """Check if client has active connection."""
        return self._connected
    
    @property
    def last_error(self) -> Optional[str]:
        """Get last error message."""
        return self._last_error
    
    @property
    def config(self) -> APIConfig:
        """Get current configuration."""
        return self._config
    
    async def connect(self) -> bool:
        """Establish connection to Control Plane API.
        
        Creates httpx.AsyncClient with connection pooling settings.
        Tests connection by calling health endpoint.
        
        Returns:
            True if connection established successfully
        """
        if self._client is not None:
            return self._connected
        
        # Create client with connection pooling
        self._client = httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=httpx.Timeout(self._config.timeout),
            limits=httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
                keepalive_expiry=30.0,
            ),
            follow_redirects=True,
        )
        
        # Test connection with health check
        try:
            await self.get_health()
            self._connected = True
            self._last_error = None
            return True
        except Exception as exc:
            self._connected = False
            self._last_error = str(exc)
            return False
    
    async def disconnect(self) -> None:
        """Close connection to Control Plane API."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._connected = False
        self._cache.clear()
    
    def _get_cached(self, key: str) -> Optional[Any]:
        """Get cached data if available and not expired."""
        entry = self._cache.get(key)
        if entry and not entry.is_expired():
            return entry.data
        return None
    
    def _set_cached(self, key: str, data: Any) -> None:
        """Cache data with TTL from config."""
        if self._config.cache_ttl > 0:
            self._cache[key] = CacheEntry(
                data=data,
                timestamp=time.time(),
                ttl=self._config.cache_ttl,
            )
    
    async def _request_with_retry(
        self,
        method: str,
        path: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Execute HTTP request with retry logic.
        
        Implements exponential backoff retry for transient failures.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (e.g., "/api/health")
            **kwargs: Additional request parameters
            
        Returns:
            Response data as dict
            
        Raises:
            httpx.HTTPStatusError: For non-retryable HTTP errors (4xx)
            httpx.ConnectError: After all retries exhausted
            httpx.TimeoutException: After all retries exhausted
        """
        if self._client is None:
            raise RuntimeError("Client not connected. Call connect() first.")
        
        last_exception: Optional[Exception] = None
        
        for attempt in range(self._config.retry_count):
            try:
                response = await self._client.request(method, path, **kwargs)
                response.raise_for_status()
                data = response.json()
                self._last_error = None
                return data
                
            except httpx.HTTPStatusError as exc:
                # Don't retry 4xx errors (client errors)
                if 400 <= exc.response.status_code < 500:
                    self._last_error = f"HTTP {exc.response.status_code}: {exc.response.text}"
                    raise
                # Retry 5xx errors (server errors)
                last_exception = exc
                self._last_error = f"HTTP {exc.response.status_code} (attempt {attempt + 1})"
                
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_exception = exc
                self._last_error = f"{type(exc).__name__} (attempt {attempt + 1})"
            
            # Exponential backoff before retry
            if attempt < self._config.retry_count - 1:
                delay = self._config.retry_delay * (2 ** attempt)
                await asyncio.sleep(delay)
        
        # All retries exhausted
        self._connected = False
        if last_exception:
            raise last_exception
        raise RuntimeError(f"Request failed after {self._config.retry_count} attempts")
    
    # === API Methods ===
    
    async def get_health(self) -> Dict[str, Any]:
        """Get system health status.
        
        GET /api/health
        
        Returns:
            Health status dict with keys:
            - status: "healthy" or "degraded"
            - postgres_ready, redis_ready, etc.
        """
        cache_key = "health"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        data = await self._request_with_retry("GET", "/api/health")
        result = data.get("data", data)
        self._set_cached(cache_key, result)
        return result
    
    async def list_workers(self) -> List[Dict[str, Any]]:
        """Get list of all registered workers.
        
        GET /api/workers
        
        Returns:
            List of worker dicts with keys:
            - worker_id, label, state, role_profile, etc.
        """
        cache_key = "workers"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        data = await self._request_with_retry("GET", "/api/workers")
        result = data.get("data", [])
        self._set_cached(cache_key, result)
        return result
    
    async def get_worker(self, worker_id: str) -> Dict[str, Any]:
        """Get detailed worker information.
        
        GET /api/workers/{worker_id}
        
        Args:
            worker_id: Worker identifier
            
        Returns:
            Worker details dict with:
            - worker data
            - health_summary
            - recent_leases, recent_events
            - drain_state, eligible_task_count
        """
        cache_key = f"worker:{worker_id}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        data = await self._request_with_retry("GET", f"/api/workers/{worker_id}")
        self._set_cached(cache_key, data)
        return data
    
    async def drain_worker(
        self,
        worker_id: str,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Drain a worker (stop accepting new tasks).
        
        POST /api/workers/{worker_id}/drain
        
        Args:
            worker_id: Worker identifier
            reason: Optional reason for draining
            
        Returns:
            Updated worker data
        """
        # Invalidate worker cache
        self._cache.pop(f"worker:{worker_id}", None)
        self._cache.pop("workers", None)
        
        payload = {"reason": reason} if reason else {}
        data = await self._request_with_retry(
            "POST",
            f"/api/workers/{worker_id}/drain",
            json=payload
        )
        return data.get("data", data)
    
    async def resume_worker(self, worker_id: str) -> Dict[str, Any]:
        """Resume a drained worker.
        
        POST /api/workers/{worker_id}/resume
        
        Args:
            worker_id: Worker identifier
            
        Returns:
            Updated worker data
        """
        # Invalidate worker cache
        self._cache.pop(f"worker:{worker_id}", None)
        self._cache.pop("workers", None)
        
        data = await self._request_with_retry(
            "POST",
            f"/api/workers/{worker_id}/resume"
        )
        return data.get("data", data)
    
    async def get_queues(self) -> List[Dict[str, Any]]:
        """Get queue status for all shards.
        
        GET /api/queues
        
        Returns:
            List of queue shard status dicts with:
            - shard: Shard name
            - depth: Queue depth
            - sample_tasks: Sample task IDs
        """
        cache_key = "queues"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        data = await self._request_with_retry("GET", "/api/queues")
        result = data.get("data", [])
        self._set_cached(cache_key, result)
        return result
    
    async def get_fleet_status(self) -> Dict[str, Any]:
        """Get overall fleet status report.
        
        GET /api/fleet/status
        
        Returns:
            Fleet status dict with:
            - worker_count, active_workers, draining_workers
            - offline_workers, unhealthy_workers
            - workers_by_role, queue_depth_by_shard
            - lease_reclaim_rate, stuck_run_count
        """
        cache_key = "fleet_status"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        data = await self._request_with_retry("GET", "/api/fleet/status")
        result = data.get("data", {})
        self._set_cached(cache_key, result)
        return result
    
    # === Utility Methods ===
    
    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._cache.clear()
    
    def invalidate_worker_cache(self, worker_id: str) -> None:
        """Invalidate cache for specific worker.
        
        Args:
            worker_id: Worker to invalidate
        """
        self._cache.pop(f"worker:{worker_id}", None)
        self._cache.pop("workers", None)


# === Convenience Factory ===

def create_client(
    base_url: str = "http://localhost:4600",
    timeout: float = 5.0,
    retry_count: int = 3,
    cache_ttl: float = 0.0,
) -> ControlPlaneClient:
    """Create a configured Control Plane API client.
    
    Args:
        base_url: Control Plane API URL
        timeout: Request timeout (seconds)
        retry_count: Number of retry attempts
        cache_ttl: Cache TTL (seconds, 0 = no caching)
        
    Returns:
        Configured ControlPlaneClient instance
    """
    config = APIConfig(
        base_url=base_url,
        timeout=timeout,
        retry_count=retry_count,
        cache_ttl=cache_ttl,
    )
    return ControlPlaneClient(config)