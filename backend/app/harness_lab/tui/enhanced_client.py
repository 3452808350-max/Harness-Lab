"""
Enhanced API Client for Harness Lab TUI.

Combines HTTP API client with WebSocket for real-time updates.
Features:
- HTTP transport for operations (drain, resume, get details)
- WebSocket transport for events (state changes, task dispatches)
- Automatic fallback to HTTP polling when WebSocket unavailable
- Unified connection management
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Callable, Awaitable, Any
from datetime import datetime

from .api_client import ControlPlaneClient, APIConfig
from .ws_client import TUIWebSocketClient, WSConfig, ConnectionState, create_ws_client


@dataclass
class HybridConfig:
    """Configuration for hybrid HTTP + WebSocket client.
    
    Attributes:
        http: HTTP API configuration
        ws: WebSocket configuration
        fallback_poll_interval: HTTP polling interval when WebSocket disconnected
        ws_healthy_threshold: Seconds without message to consider WS unhealthy
    """
    http: APIConfig = None
    ws: WSConfig = None
    fallback_poll_interval: float = 30.0
    ws_healthy_threshold: float = 60.0
    
    def __post_init__(self):
        if self.http is None:
            self.http = APIConfig()
        if self.ws is None:
            self.ws = WSConfig()


class EnhancedAPIClient:
    """API client with HTTP operations + WebSocket events.
    
    Architecture:
    - HTTP: Used for operations (drain, resume, get details, get health)
    - WebSocket: Used for real-time event notifications
    
    Connection Modes:
    - "full": Both HTTP and WebSocket connected
    - "websocket_only": WebSocket connected, HTTP not needed
    - "http_only": Only HTTP connected (WebSocket failed)
    - "disconnected": Neither connected
    
    Usage:
        client = EnhancedAPIClient(
            HybridConfig(
                http=APIConfig(base_url="http://localhost:4600"),
                ws=WSConfig(url="ws://localhost:4600/ws")
            )
        )
        await client.connect()
        
        # HTTP operations
        result = await client.drain_worker("w-01")
        
        # WebSocket events handled via callbacks
        client.set_event_handler(on_worker_event)
        
        # Check connection mode
        if client.mode == "http_only":
            # WebSocket unavailable, using HTTP polling
            pass
    """
    
    def __init__(self, config: HybridConfig = None):
        """Initialize enhanced client.
        
        Args:
            config: Hybrid configuration (uses defaults if not provided)
        """
        self._config = config or HybridConfig()
        
        # HTTP client for operations
        self._http: ControlPlaneClient = ControlPlaneClient(self._config.http)
        
        # WebSocket client for events
        self._ws: TUIWebSocketClient = create_ws_client(self._config.ws)
        
        # Connection state
        self._ws_connected = False
        self._http_connected = False
        
        # Event handler
        self._event_handler: Optional[Callable[[Dict], Awaitable[None]]] = None
        
        # Fallback polling
        self._poll_task: Optional[asyncio.Task] = None
        self._poll_running = False
        
        # Cached state from WebSocket
        self._cached_health: Optional[Dict] = None
        self._cached_workers: Optional[List[Dict]] = None
        self._cached_queues: Optional[List[Dict]] = None
    
    @property
    def is_connected(self) -> bool:
        """Check if at least one transport is connected."""
        return self._ws_connected or self._http_connected
    
    @property
    def mode(self) -> str:
        """Get current connection mode.
        
        Returns:
            "full" | "websocket_only" | "http_only" | "disconnected"
        """
        if self._ws_connected and self._http_connected:
            return "full"
        elif self._ws_connected:
            return "websocket_only"
        elif self._http_connected:
            return "http_only"
        else:
            return "disconnected"
    
    @property
    def ws_state(self) -> ConnectionState:
        """Get WebSocket connection state."""
        return self._ws.state
    
    @property
    def http_connected(self) -> bool:
        """Check HTTP client connection."""
        return self._http_connected
    
    @property
    def last_error(self) -> Optional[str]:
        """Get last error from either transport."""
        ws_error = self._ws.last_error
        http_error = self._http.last_error
        
        if ws_error and http_error:
            return f"WS: {ws_error}; HTTP: {http_error}"
        return ws_error or http_error
    
    async def connect(self) -> bool:
        """Connect both transports.
        
        Returns:
            True if at least one transport connected
        """
        # Connect HTTP first (needed for operations)
        http_ok = await self._http.connect()
        self._http_connected = http_ok
        
        # Set WebSocket handlers
        self._ws.set_connection_handler(self._handle_ws_state)
        self._ws.set_message_handler(self._handle_ws_message)
        
        # Start WebSocket connection
        await self._ws.start()
        
        # Give WebSocket a moment to connect
        await asyncio.sleep(1.0)
        
        # If WebSocket failed, start HTTP fallback polling
        if not self._ws_connected and self._http_connected:
            self._start_fallback_polling()
        
        return self.is_connected
    
    async def disconnect(self) -> None:
        """Disconnect both transports."""
        # Stop fallback polling
        self._stop_fallback_polling()
        
        # Disconnect WebSocket
        await self._ws.stop()
        self._ws_connected = False
        
        # Disconnect HTTP
        await self._http.disconnect()
        self._http_connected = False
    
    def set_event_handler(self, handler: Callable[[Dict], Awaitable[None]]) -> None:
        """Set handler for WebSocket events.
        
        Args:
            handler: Async function to process events
        """
        self._event_handler = handler
    
    # === WebSocket Handlers ===
    
    async def _handle_ws_state(self, state: ConnectionState) -> None:
        """Handle WebSocket connection state changes."""
        old_connected = self._ws_connected
        self._ws_connected = (state == ConnectionState.CONNECTED)
        
        # State transition handling
        if old_connected and not self._ws_connected:
            # WebSocket disconnected - start fallback
            if self._http_connected:
                self._start_fallback_polling()
        
        elif not old_connected and self._ws_connected:
            # WebSocket reconnected - stop fallback
            self._stop_fallback_polling()
    
    async def _handle_ws_message(self, data: Dict) -> None:
        """Handle WebSocket message."""
        # Cache state from WebSocket
        event_type = data.get("event_type")
        event_data = data.get("data", {})
        
        if event_type == "initial_state":
            self._cached_health = event_data.get("health")
            self._cached_workers = event_data.get("workers")
            self._cached_queues = event_data.get("queues")
        elif event_type == "health_update":
            self._cached_health = event_data
        elif event_type == "worker_joined":
            # Update cached workers
            if self._cached_workers is not None:
                self._cached_workers.append(event_data)
        elif event_type == "worker_left":
            # Remove from cached workers
            if self._cached_workers is not None:
                worker_id = event_data.get("worker_id")
                self._cached_workers = [
                    w for w in self._cached_workers
                    if w.get("worker_id") != worker_id
                ]
        elif event_type == "worker_state_changed":
            # Update cached worker state
            if self._cached_workers is not None:
                worker_id = event_data.get("worker_id")
                new_state = event_data.get("new_state")
                for w in self._cached_workers:
                    if w.get("worker_id") == worker_id:
                        w["state"] = new_state
        
        # Forward to event handler
        if self._event_handler:
            try:
                await self._event_handler(data)
            except Exception:
                pass  # Don't propagate handler errors
    
    # === HTTP Operations ===
    
    async def drain_worker(self, worker_id: str, reason: str = None) -> Dict:
        """Drain worker via HTTP.
        
        POST /api/workers/{worker_id}/drain
        
        Args:
            worker_id: Worker identifier
            reason: Optional drain reason
            
        Returns:
            Updated worker data
        """
        if not self._http_connected:
            raise RuntimeError("HTTP client not connected")
        
        # Invalidate caches
        self._cached_workers = None
        
        return await self._http.drain_worker(worker_id, reason)
    
    async def resume_worker(self, worker_id: str) -> Dict:
        """Resume worker via HTTP.
        
        POST /api/workers/{worker_id}/resume
        
        Args:
            worker_id: Worker identifier
            
        Returns:
            Updated worker data
        """
        if not self._http_connected:
            raise RuntimeError("HTTP client not connected")
        
        # Invalidate caches
        self._cached_workers = None
        
        return await self._http.resume_worker(worker_id)
    
    async def get_worker(self, worker_id: str) -> Dict:
        """Get worker details via HTTP.
        
        GET /api/workers/{worker_id}
        
        Args:
            worker_id: Worker identifier
            
        Returns:
            Worker details dict
        """
        if not self._http_connected:
            raise RuntimeError("HTTP client not connected")
        
        return await self._http.get_worker(worker_id)
    
    async def get_health(self) -> Dict:
        """Get health status.
        
        Uses cached WebSocket data if available, otherwise HTTP.
        
        Returns:
            Health status dict
        """
        if self._cached_health is not None:
            return self._cached_health
        
        if self._http_connected:
            return await self._http.get_health()
        
        raise RuntimeError("No transport available")
    
    async def list_workers(self) -> List[Dict]:
        """List all workers.
        
        Uses cached WebSocket data if available, otherwise HTTP.
        
        Returns:
            List of worker dicts
        """
        if self._cached_workers is not None:
            return self._cached_workers
        
        if self._http_connected:
            return await self._http.list_workers()
        
        raise RuntimeError("No transport available")
    
    async def get_queues(self) -> List[Dict]:
        """Get queue status.
        
        Uses cached WebSocket data if available, otherwise HTTP.
        
        Returns:
            List of queue shard dicts
        """
        if self._cached_queues is not None:
            return self._cached_queues
        
        if self._http_connected:
            return await self._http.get_queues()
        
        raise RuntimeError("No transport available")
    
    async def get_fleet_status(self) -> Dict:
        """Get fleet status via HTTP.
        
        Returns:
            Fleet status dict
        """
        if self._http_connected:
            return await self._http.get_fleet_status()
        
        raise RuntimeError("HTTP client not connected")
    
    # === Fallback Polling ===
    
    def _start_fallback_polling(self) -> None:
        """Start HTTP polling fallback."""
        if self._poll_running:
            return
        
        self._poll_running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
    
    def _stop_fallback_polling(self) -> None:
        """Stop HTTP polling fallback."""
        self._poll_running = False
        
        if self._poll_task:
            self._poll_task.cancel()
            try:
                asyncio.get_running_loop().run_until_complete(self._poll_task)
            except (asyncio.CancelledError, RuntimeError):
                pass
            self._poll_task = None
    
    async def _poll_loop(self) -> None:
        """HTTP polling loop for fallback."""
        while self._poll_running and self._http_connected:
            try:
                # Fetch data via HTTP
                health = await self._http.get_health()
                workers = await self._http.list_workers()
                queues = await self._http.get_queues()
                
                # Update caches
                self._cached_health = health
                self._cached_workers = workers
                self._cached_queues = queues
                
                # Notify via event handler
                if self._event_handler:
                    await self._event_handler({
                        "event_type": "fallback_poll",
                        "timestamp": time.time(),
                        "data": {
                            "health": health,
                            "workers": workers,
                            "queues": queues,
                        }
                    })
                
            except Exception as e:
                # Log error but continue polling
                if self._event_handler:
                    await self._event_handler({
                        "event_type": "fallback_error",
                        "timestamp": time.time(),
                        "data": {"error": str(e)}
                    })
            
            # Wait for next poll interval
            await asyncio.sleep(self._config.fallback_poll_interval)
    
    # === Utility Methods ===
    
    def clear_cache(self) -> None:
        """Clear all cached state."""
        self._cached_health = None
        self._cached_workers = None
        self._cached_queues = None
        self._http.clear_cache()
    
    def invalidate_worker_cache(self, worker_id: str) -> None:
        """Invalidate worker cache."""
        self._cached_workers = None
        self._http.invalidate_worker_cache(worker_id)
    
    async def request_initial_state(self) -> None:
        """Request initial state via WebSocket.
        
        Sends a request to the server to get the current state snapshot.
        Useful when returning from a sub-screen to refresh data.
        """
        if self._ws_connected:
            await self._ws.send({"event_type": "request_initial_state"})


def create_enhanced_client(
    api_url: str = "http://localhost:4600",
    ws_url: str = None,
    **kwargs
) -> EnhancedAPIClient:
    """Factory function to create enhanced API client.
    
    Derives WebSocket URL from HTTP URL if not provided.
    
    Args:
        api_url: HTTP API base URL
        ws_url: WebSocket URL (default: derived from api_url)
        **kwargs: Additional config options (timeout, retry_count, etc.)
        
    Returns:
        EnhancedAPIClient instance
    """
    http_config = APIConfig(base_url=api_url)
    
    # Pass kwargs to HTTP config
    if "timeout" in kwargs:
        http_config.timeout = kwargs["timeout"]
    if "retry_count" in kwargs:
        http_config.retry_count = kwargs["retry_count"]
    if "retry_delay" in kwargs:
        http_config.retry_delay = kwargs["retry_delay"]
    if "cache_ttl" in kwargs:
        http_config.cache_ttl = kwargs["cache_ttl"]
    
    # Derive WebSocket URL
    if ws_url is None:
        ws_url = api_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = ws_url.rstrip("/") + "/ws"
    
    ws_config = WSConfig(url=ws_url)
    
    # Pass kwargs to WS config
    if "ws_reconnect_delay" in kwargs:
        ws_config.reconnect_delay = kwargs["ws_reconnect_delay"]
    if "ws_max_retries" in kwargs:
        ws_config.max_retries = kwargs["ws_max_retries"]
    if "fallback_poll_interval" in kwargs:
        fallback_interval = kwargs["fallback_poll_interval"]
    else:
        fallback_interval = 30.0
    
    config = HybridConfig(
        http=http_config,
        ws=ws_config,
        fallback_poll_interval=fallback_interval,
    )
    
    return EnhancedAPIClient(config)