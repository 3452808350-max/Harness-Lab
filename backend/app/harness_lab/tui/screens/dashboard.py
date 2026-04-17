"""
Dashboard screen - Main control plane TUI view.

Shows:
- Service status panel (left)
- Worker table (center/right)
- Event stream (bottom)

WebSocket Integration:
- Uses EnhancedAPIClient for HTTP + WebSocket hybrid
- Receives real-time events via WebSocket
- Falls back to HTTP polling when WebSocket disconnected
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Dict, List, Optional

from textual.screen import Screen
from textual.widgets import Header
from textual.containers import Container, Horizontal
from textual.app import ComposeResult
from textual.reactive import reactive

from ..widgets import ServicePanel, WorkerTable, StatusBar, EventStream, QueuePanel
from ..theme import ColorTheme
from ..api_client import ControlPlaneClient, APIConfig
from ..enhanced_client import EnhancedAPIClient, HybridConfig, create_enhanced_client
from ..ws_client import ConnectionState
from ..ws_worker import (
    WSWorkerJoined,
    WSWorkerLeft,
    WSWorkerStateChanged,
    WSTaskDispatched,
    WSTaskCompleted,
    WSHealthUpdate,
    WSQueueUpdate,
    WSInitialState,
    WSHeartbeat,
    WSConnectionState,
)
from .workers import WorkerDetailScreen


class DashboardScreen(Screen):
    """Main dashboard screen for Control Plane TUI.
    
    Layout:
    ┌───────────────────────────────────────┐
    │ Header                                 │
    ├───────────────────────────────────────┤
    │ ┌───────────┐ ┌─────────────────────┐ │
    │ │ Services  │ │ Workers              │ │
    │ │ Panel     │ │ Table                │ │
    │ └───────────┘ └─────────────────────┘ │
    │ ┌─────────────────────────────────────┐│
    │ │ Event Stream                         ││
    │ └─────────────────────────────────────┘│
    ├───────────────────────────────────────┤
    │ Status Bar                             │
    └───────────────────────────────────────┘
    
    WebSocket Mode:
    - Real-time updates via WebSocket events
    - Typed message handlers: on_ws_worker_joined, etc.
    - HTTP fallback when WebSocket disconnected
    """
    
    DEFAULT_CSS = """
    DashboardScreen {
        layout: vertical;
    }
    
    DashboardScreen > Header {
        height: 1;
    }
    
    DashboardScreen > Container {
        height: auto;
    }
    
    DashboardScreen > Horizontal {
        height: 1fr;
    }
    
    DashboardScreen #service-panel {
        width: 25;
    }
    
    DashboardScreen #queue-panel {
        width: 30;
    }
    
    DashboardScreen #worker-table {
        width: 1fr;
    }
    
    DashboardScreen > EventStream {
        height: 8;
    }
    """
    
    BINDINGS = [
        ("ctrl+h", "show_help", "Help"),
        ("escape", "quit", "Exit"),
        ("d", "drain_worker", "Drain"),
        ("r", "resume_worker", "Resume"),
        ("i", "inspect_worker", "Inspect"),
        ("l", "toggle_logs", "Logs"),
        ("q", "quit", "Quit"),
    ]
    
    # Worker state color mapping
    STATE_COLORS = {
        "idle": "🟢",
        "leased": "🟡",
        "executing": "🟡",
        "draining": "🟡",
        "offline": "⚫",
        "unhealthy": "🔴",
        "registering": "🔵",
    }
    
    # Reactive state for updating display
    workers_count: reactive[int] = reactive(0)
    tasks_count: reactive[int] = reactive(0)
    
    def __init__(
        self,
        theme: ColorTheme = None,
        api_url: str = "http://localhost:4600",
        ws_url: str = None,
        use_mock_ws: bool = False,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.theme = theme or ColorTheme.dark()
        self.api_url = api_url
        self.ws_url = ws_url
        self.use_mock_ws = use_mock_ws
        
        # Enhanced API client (HTTP + WebSocket)
        self._enhanced_client: Optional[EnhancedAPIClient] = None
        
        # HTTP client for operations (kept for backwards compatibility)
        self._api_config = APIConfig(base_url=api_url)
        self._api_client: Optional[ControlPlaneClient] = None
        
        # Worker state cache for tracking changes
        self._workers_cache: Dict[str, Dict] = {}
        
        # Connection state
        self._ws_connected = False
        self._http_connected = False
        self._using_demo_data = False
        
        # Fallback polling timer
        self._fallback_timer = None
    
    def compose(self) -> ComposeResult:
        """Compose dashboard layout."""
        yield Header(id="header")
        
        with Container(id="main"):
            with Horizontal(id="top-panels"):
                yield ServicePanel(self.theme, id="service-panel")
                yield QueuePanel(self.theme, id="queue-panel")
                yield WorkerTable(self.theme, id="worker-table")
            
            yield EventStream(self.theme, id="event-stream")
        
        yield StatusBar(self.theme, id="status-bar")
    
    def on_mount(self) -> None:
        """Initialize dashboard on mount."""
        # Set header title
        header = self.query_one("#header", Header)
        header.title = "Harness Lab Control Plane"
        
        # Initialize enhanced client connection (async)
        self._init_enhanced_connection()
    
    def on_unmount(self) -> None:
        """Cleanup on unmount."""
        # Stop fallback timer
        if self._fallback_timer:
            self._fallback_timer.stop()
            self._fallback_timer = None
        
        # Disconnect enhanced client (async cleanup)
        if self._enhanced_client:
            asyncio.create_task(self._enhanced_client.disconnect())
        
        if self._api_client:
            asyncio.create_task(self._api_client.disconnect())
    
    def _init_enhanced_connection(self) -> None:
        """Initialize enhanced API client connection."""
        # Create enhanced client with HTTP + WebSocket
        self._enhanced_client = create_enhanced_client(
            api_url=self.api_url,
            ws_url=self.ws_url,
            fallback_poll_interval=30.0,
        )
        
        # Set event handler for WebSocket events
        self._enhanced_client.set_event_handler(self._handle_ws_event)
        
        # Start async connection
        asyncio.create_task(self._connect_enhanced())
    
    async def _connect_enhanced(self) -> None:
        """Connect to enhanced client asynchronously."""
        try:
            connected = await self._enhanced_client.connect()
            if connected:
                self._http_connected = True
                self._ws_connected = self._enhanced_client.ws_state == ConnectionState.CONNECTED
                self._using_demo_data = False
                
                # Keep HTTP client reference for operations
                self._api_client = self._enhanced_client._http
                
                # Log successful connection
                events = self.query_one("#event-stream", EventStream)
                mode = self._enhanced_client.mode
                events.add_event(
                    datetime.now().strftime("%H:%M:%S"),
                    "CONNECT",
                    f"Connected ({mode}): {self.api_url}"
                )
                
                # If WebSocket not connected, start fallback polling
                if not self._ws_connected:
                    self._start_http_fallback()
            else:
                self._http_connected = False
                self._ws_connected = False
                self._using_demo_data = True
                
                # Log connection failure and use demo data
                events = self.query_one("#event-stream", EventStream)
                error = self._enhanced_client.last_error or "Unknown error"
                events.add_event(
                    datetime.now().strftime("%H:%M:%S"),
                    "ERROR",
                    f"Connection failed: {error}"
                )
                events.add_event(
                    datetime.now().strftime("%H:%M:%S"),
                    "INFO",
                    "Using demo data fallback"
                )
                
                # Initialize with demo data
                self._init_demo_data()
        except Exception as e:
            self._http_connected = False
            self._ws_connected = False
            self._using_demo_data = True
            
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "ERROR",
                f"Connection error: {str(e)}"
            )
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "INFO",
                "Using demo data fallback"
            )
            
            self._init_demo_data()
    
    # === WebSocket Event Handlers ===
    
    async def _handle_ws_event(self, data: Dict) -> None:
        """Handle WebSocket event from enhanced client."""
        event_type = data.get("event_type", "unknown")
        
        # Log all events
        events = self.query_one("#event-stream", EventStream)
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        if event_type == "initial_state":
            await self._handle_initial_state(data.get("data", {}))
            events.add_event(timestamp, "INIT", "Initial state loaded")
            
        elif event_type == "worker_joined":
            await self._handle_worker_joined(data.get("data", {}))
            
        elif event_type == "worker_left":
            await self._handle_worker_left(data.get("data", {}))
            
        elif event_type == "worker_state_changed":
            await self._handle_worker_state_changed(data.get("data", {}))
            
        elif event_type == "task_dispatched":
            await self._handle_task_dispatched(data.get("data", {}))
            
        elif event_type == "task_completed":
            await self._handle_task_completed(data.get("data", {}))
            
        elif event_type == "health_update":
            await self._handle_health_update(data.get("data", {}))
            
        elif event_type == "queue_update":
            await self._handle_queue_update(data.get("data", {}))
            
        elif event_type == "heartbeat":
            # Heartbeat received - connection healthy
            events.add_event(timestamp, "HEARTBEAT", "WS connection healthy")
            
        elif event_type == "fallback_poll":
            # HTTP fallback poll result
            await self._handle_fallback_poll(data.get("data", {}))
            events.add_event(timestamp, "POLL", "HTTP fallback update")
            
        elif event_type == "fallback_error":
            # HTTP fallback error
            error = data.get("data", {}).get("error", "Unknown")
            events.add_event(timestamp, "ERROR", f"Fallback error: {error}")
    
    # === Typed WebSocket Message Handlers (App-level) ===
    
    def on_ws_worker_joined(self, message: WSWorkerJoined) -> None:
        """Handle WSWorkerJoined message."""
        asyncio.create_task(self._handle_worker_joined({
            "worker_id": message.worker_id,
            "role": message.role,
            "state": message.state,
        }))
    
    def on_ws_worker_left(self, message: WSWorkerLeft) -> None:
        """Handle WSWorkerLeft message."""
        asyncio.create_task(self._handle_worker_left({
            "worker_id": message.worker_id,
        }))
    
    def on_ws_worker_state_changed(self, message: WSWorkerStateChanged) -> None:
        """Handle WSWorkerStateChanged message."""
        asyncio.create_task(self._handle_worker_state_changed({
            "worker_id": message.worker_id,
            "old_state": message.old_state,
            "new_state": message.new_state,
            "task_id": message.task_id,
        }))
    
    def on_ws_task_dispatched(self, message: WSTaskDispatched) -> None:
        """Handle WSTaskDispatched message."""
        asyncio.create_task(self._handle_task_dispatched({
            "task_id": message.task_id,
            "worker_id": message.worker_id,
        }))
    
    def on_ws_task_completed(self, message: WSTaskCompleted) -> None:
        """Handle WSTaskCompleted message."""
        asyncio.create_task(self._handle_task_completed({
            "task_id": message.task_id,
            "worker_id": message.worker_id,
            "success": message.success,
        }))
    
    def on_ws_health_update(self, message: WSHealthUpdate) -> None:
        """Handle WSHealthUpdate message."""
        asyncio.create_task(self._handle_health_update(message.health_data))
    
    def on_ws_queue_update(self, message: WSQueueUpdate) -> None:
        """Handle WSQueueUpdate message."""
        asyncio.create_task(self._handle_queue_update(message.queues_data))
    
    def on_ws_initial_state(self, message: WSInitialState) -> None:
        """Handle WSInitialState message."""
        asyncio.create_task(self._handle_initial_state({
            "health": message.health,
            "workers": message.workers,
            "queues": message.queues,
        }))
    
    def on_ws_heartbeat(self, message: WSHeartbeat) -> None:
        """Handle WSHeartbeat message."""
        events = self.query_one("#event-stream", EventStream)
        events.add_event(
            datetime.now().strftime("%H:%M:%S"),
            "HEARTBEAT",
            "WS connection healthy"
        )
    
    def on_ws_connection_state(self, message: WSConnectionState) -> None:
        """Handle WSConnectionState message."""
        events = self.query_one("#event-stream", EventStream)
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        if message.state == ConnectionState.CONNECTED:
            self._ws_connected = True
            events.add_event(timestamp, "WS_CONNECTED", "WebSocket connected")
            # Stop fallback polling
            self._stop_http_fallback()
            
        elif message.state == ConnectionState.DISCONNECTED:
            self._ws_connected = False
            events.add_event(timestamp, "WS_DISCONNECTED", "WebSocket disconnected")
            # Start fallback polling if HTTP still connected
            if self._http_connected:
                self._start_http_fallback()
                
        elif message.state == ConnectionState.RECONNECTING:
            events.add_event(timestamp, "WS_RECONNECT", f"Reconnecting (attempt {message.retry_count})")
            
        elif message.state == ConnectionState.ERROR:
            self._ws_connected = False
            events.add_event(timestamp, "WS_ERROR", f"WebSocket error: {message.error}")
            # Start fallback polling if HTTP still connected
            if self._http_connected:
                self._start_http_fallback()
    
    # === WebSocket Event Processing ===
    
    async def _handle_initial_state(self, data: Dict) -> None:
        """Handle initial state snapshot."""
        # Update services
        health = data.get("health", {})
        self._update_services(health)
        
        # Update workers
        workers = data.get("workers", [])
        self._sync_workers(workers)
        
        # Update queues
        queues = data.get("queues", [])
        self._update_queues(queues)
    
    async def _handle_worker_joined(self, data: Dict) -> None:
        """Handle worker joined event."""
        worker_id = data.get("worker_id")
        role = data.get("role", "general")
        state = data.get("state", "idle")
        
        if not worker_id:
            return
        
        workers_table = self.query_one("#worker-table", WorkerTable)
        events = self.query_one("#event-stream", EventStream)
        
        # Add to table and cache
        workers_table.add_worker(worker_id, state, role, "-", "-")
        self._workers_cache[worker_id] = {
            "state": state, "role": role, "task": "-", "lease": "-"
        }
        
        # Log event
        events.add_event(
            datetime.now().strftime("%H:%M:%S"),
            "REGISTER",
            f"{worker_id} ({role}) joined"
        )
        
        # Update counter
        self.workers_count = len(self._workers_cache)
    
    async def _handle_worker_left(self, data: Dict) -> None:
        """Handle worker left event."""
        worker_id = data.get("worker_id")
        
        if not worker_id:
            return
        
        workers_table = self.query_one("#worker-table", WorkerTable)
        events = self.query_one("#event-stream", EventStream)
        
        # Remove from table and cache
        if worker_id in self._workers_cache:
            workers_table.remove_worker(worker_id)
            del self._workers_cache[worker_id]
            
            # Log event
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "OFFLINE",
                f"{worker_id} left"
            )
        
        # Update counter
        self.workers_count = len(self._workers_cache)
    
    async def _handle_worker_state_changed(self, data: Dict) -> None:
        """Handle worker state change event."""
        worker_id = data.get("worker_id")
        old_state = data.get("old_state")
        new_state = data.get("new_state")
        task_id = data.get("task_id")
        
        if not worker_id:
            return
        
        workers_table = self.query_one("#worker-table", WorkerTable)
        events = self.query_one("#event-stream", EventStream)
        
        # Update table and cache
        lease = "active" if task_id else "-"
        workers_table.update_worker(worker_id, new_state, task_id or "-", lease)
        
        if worker_id in self._workers_cache:
            self._workers_cache[worker_id]["state"] = new_state
            self._workers_cache[worker_id]["task"] = task_id or "-"
            self._workers_cache[worker_id]["lease"] = lease
        
        # Log event
        events.add_event(
            datetime.now().strftime("%H:%M:%S"),
            "STATE",
            f"{worker_id}: {old_state} → {new_state}"
        )
    
    async def _handle_task_dispatched(self, data: Dict) -> None:
        """Handle task dispatched event."""
        task_id = data.get("task_id")
        worker_id = data.get("worker_id")
        
        if not task_id or not worker_id:
            return
        
        events = self.query_one("#event-stream", EventStream)
        
        # Log event
        events.add_event(
            datetime.now().strftime("%H:%M:%S"),
            "DISPATCH",
            f"{task_id} → {worker_id}"
        )
        
        # Update task counter
        self.tasks_count += 1
    
    async def _handle_task_completed(self, data: Dict) -> None:
        """Handle task completed event."""
        task_id = data.get("task_id")
        worker_id = data.get("worker_id")
        success = data.get("success", True)
        
        if not task_id or not worker_id:
            return
        
        events = self.query_one("#event-stream", EventStream)
        
        # Log event
        status = "✓" if success else "✗"
        events.add_event(
            datetime.now().strftime("%H:%M:%S"),
            "COMPLETE",
            f"{task_id} {status} ({worker_id})"
        )
    
    async def _handle_health_update(self, data: Dict) -> None:
        """Handle health update event."""
        self._update_services(data)
    
    async def _handle_queue_update(self, data: List) -> None:
        """Handle queue update event."""
        self._update_queues(data)
    
    async def _handle_fallback_poll(self, data: Dict) -> None:
        """Handle HTTP fallback poll result."""
        # Same as initial state
        await self._handle_initial_state(data)
    
    # === HTTP Fallback ===
    
    def _start_http_fallback(self) -> None:
        """Start HTTP polling fallback when WebSocket disconnected."""
        if self._fallback_timer:
            return  # Already running
        
        events = self.query_one("#event-stream", EventStream)
        events.add_event(
            datetime.now().strftime("%H:%M:%S"),
            "INFO",
            "Starting HTTP fallback polling"
        )
        
        # Poll every 30 seconds
        self._fallback_timer = self.set_interval(30.0, self._http_poll_fallback)
    
    def _stop_http_fallback(self) -> None:
        """Stop HTTP polling fallback."""
        if self._fallback_timer:
            self._fallback_timer.stop()
            self._fallback_timer = None
            
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "INFO",
                "Stopped HTTP fallback (WebSocket connected)"
            )
    
    async def _http_poll_fallback(self) -> None:
        """HTTP polling fallback when WebSocket unavailable."""
        if not self._http_connected or not self._enhanced_client:
            return
        
        try:
            # Fetch via HTTP (enhanced client already handles caching)
            health = await self._enhanced_client.get_health()
            workers = await self._enhanced_client.list_workers()
            queues = await self._enhanced_client.get_queues()
            
            # Update displays
            self._update_services(health)
            self._sync_workers(workers)
            self._update_queues(queues)
            
        except Exception as e:
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "ERROR",
                f"Fallback poll error: {str(e)}"
            )
    
    def _init_demo_data(self) -> None:
        """Initialize with demo/test data (fallback when API unavailable)."""
        # Service statuses - show as running in demo mode
        services = self.query_one("#service-panel", ServicePanel)
        services.update_all({
            "PostgreSQL 16": "running",
            "Redis 7": "running",
            "Docker CE": "running",
            "API": "warning",
        })
        
        # Workers (demo data)
        workers = self.query_one("#worker-table", WorkerTable)
        workers.clear_workers()
        workers.add_worker("w-01", "executing", "executor", "T-42", "30s")
        workers.add_worker("w-02", "idle", "general", "-", "-")
        workers.add_worker("w-03", "draining", "reviewer", "T-41", "5s")
        workers.add_worker("w-04", "offline", "planner", "-", "-")
        
        # Events
        events = self.query_one("#event-stream", EventStream)
        events.add_event("22:00:01", "DISPATCH", "T-42 → w-01 (executor)")
        events.add_event("22:00:00", "HEARTBEAT", "w-02 (idle)")
        events.add_event("21:59:55", "LEASE", "w-03 expired")
        
        # Update counters
        self.workers_count = 4
        self.tasks_count = 21
        
        # Cache demo workers
        self._workers_cache = {
            "w-01": {"state": "executing", "role": "executor", "task": "T-42", "lease": "30s"},
            "w-02": {"state": "idle", "role": "general", "task": "-", "lease": "-"},
            "w-03": {"state": "draining", "role": "reviewer", "task": "T-41", "lease": "5s"},
            "w-04": {"state": "offline", "role": "planner", "task": "-", "lease": "-"},
        }
    
    def _update_services(self, health_data: Dict) -> None:
        """Update service panel based on health data."""
        services = self.query_one("#service-panel", ServicePanel)
        
        # Map health fields to service names
        postgres_status = "running" if health_data.get("postgres_ready", False) else "stopped"
        redis_status = "running" if health_data.get("redis_ready", False) else "stopped"
        docker_status = "running" if health_data.get("docker_ready", False) else "stopped"
        api_status = "running" if health_data.get("status") == "healthy" else "warning"
        
        services.update_all({
            "PostgreSQL 16": postgres_status,
            "Redis 7": redis_status,
            "Docker CE": docker_status,
            "API": api_status,
        })
        
        # Update counters from health data
        self.workers_count = health_data.get("workers", 0)
        
        # Use queue depth as tasks count if available
        ready_queue = health_data.get("ready_queue_depth", 0)
        if ready_queue > 0:
            self.tasks_count = ready_queue
    
    def _update_queues(self, queues_data: List[Dict]) -> None:
        """Update queue panel based on queue data."""
        queues_panel = self.query_one("#queue-panel", QueuePanel)
        queues_panel.update_shards(queues_data)
        
        # Update tasks count from queue depth
        total_depth = queues_panel.get_total_depth()
        if total_depth > 0:
            self.tasks_count = total_depth
    
    def _sync_workers(self, workers_data: List[Dict]) -> None:
        """Sync worker table with API data.
        
        Handles:
        - Adding new workers
        - Removing disconnected workers
        - Updating existing worker states
        """
        workers_table = self.query_one("#worker-table", WorkerTable)
        
        # Build current workers set
        current_ids = set(self._workers_cache.keys())
        new_ids = {w.get("worker_id") for w in workers_data if w.get("worker_id")}
        
        # Remove workers that no longer exist
        removed_ids = current_ids - new_ids
        for worker_id in removed_ids:
            workers_table.remove_worker(worker_id)
            del self._workers_cache[worker_id]
            
            # Log removal
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "OFFLINE",
                f"{worker_id} removed"
            )
        
        # Add or update workers
        for worker in workers_data:
            worker_id = worker.get("worker_id")
            if not worker_id:
                continue
            
            # Extract worker info
            state = worker.get("state", "unknown")
            role = worker.get("role_profile", "general") or "general"
            task = worker.get("current_run_id") or "-"
            lease_id = worker.get("current_lease_id")
            
            # Calculate lease remaining (approximate)
            lease = "-" if not lease_id else "active"
            
            # Check if new worker
            if worker_id not in self._workers_cache:
                workers_table.add_worker(worker_id, state, role, task, lease)
                self._workers_cache[worker_id] = {
                    "state": state, "role": role, "task": task, "lease": lease
                }
                
                # Log new worker
                events = self.query_one("#event-stream", EventStream)
                events.add_event(
                    datetime.now().strftime("%H:%M:%S"),
                    "REGISTER",
                    f"{worker_id} ({role}) joined"
                )
            else:
                # Check for state changes
                cached = self._workers_cache[worker_id]
                if cached["state"] != state:
                    # Log state change
                    events = self.query_one("#event-stream", EventStream)
                    events.add_event(
                        datetime.now().strftime("%H:%M:%S"),
                        "STATE",
                        f"{worker_id}: {cached['state']} → {state}"
                    )
                
                # Update table and cache
                workers_table.update_worker(worker_id, state, task, lease)
                self._workers_cache[worker_id] = {
                    "state": state, "role": role, "task": task, "lease": lease
                }
    
    def action_show_help(self) -> None:
        """Show help overlay."""
        self.app.push_screen("help")
    
    async def action_drain_worker(self) -> None:
        """Drain selected worker.
        
        Calls API drain endpoint and logs result.
        """
        workers_table = self.query_one("#worker-table", WorkerTable)
        
        # Get selected worker (if cursor on a row)
        cursor_row = workers_table.cursor_coordinate.row if workers_table.cursor_coordinate else None
        if cursor_row is None:
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "ERROR",
                "No worker selected"
            )
            return
        
        # Get worker ID from row key
        worker_id = None
        for i, (key, _) in enumerate(workers_table.rows.items()):
            if i == cursor_row:
                worker_id = key
                break
        
        if not worker_id:
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "ERROR",
                "Could not get worker ID"
            )
            return
        
        events = self.query_one("#event-stream", EventStream)
        
        # Use enhanced client if available
        client = self._enhanced_client or self._api_client
        
        if self._http_connected and client:
            try:
                result = await client.drain_worker(worker_id, reason="Manual drain via TUI")
                new_state = result.get("state", "draining")
                
                # Update cache and table
                if worker_id in self._workers_cache:
                    self._workers_cache[worker_id]["state"] = new_state
                workers_table.update_worker(worker_id, new_state)
                
                events.add_event(
                    datetime.now().strftime("%H:%M:%S"),
                    "DRAIN",
                    f"{worker_id} → draining"
                )
            except Exception as e:
                events.add_event(
                    datetime.now().strftime("%H:%M:%S"),
                    "ERROR",
                    f"Drain failed: {str(e)}"
                )
        else:
            # Demo mode
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "DRAIN",
                f"{worker_id} → draining (demo)"
            )
            
            # Update demo state
            if worker_id in self._workers_cache:
                self._workers_cache[worker_id]["state"] = "draining"
            workers_table.update_worker(worker_id, "draining")
    
    async def action_resume_worker(self) -> None:
        """Resume selected worker.
        
        Calls API resume endpoint and logs result.
        """
        workers_table = self.query_one("#worker-table", WorkerTable)
        
        # Get selected worker (if cursor on a row)
        cursor_row = workers_table.cursor_coordinate.row if workers_table.cursor_coordinate else None
        if cursor_row is None:
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "ERROR",
                "No worker selected"
            )
            return
        
        # Get worker ID from row key
        worker_id = None
        for i, (key, _) in enumerate(workers_table.rows.items()):
            if i == cursor_row:
                worker_id = key
                break
        
        if not worker_id:
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "ERROR",
                "Could not get worker ID"
            )
            return
        
        events = self.query_one("#event-stream", EventStream)
        
        # Use enhanced client if available
        client = self._enhanced_client or self._api_client
        
        if self._http_connected and client:
            try:
                result = await client.resume_worker(worker_id)
                new_state = result.get("state", "idle")
                
                # Update cache and table
                if worker_id in self._workers_cache:
                    self._workers_cache[worker_id]["state"] = new_state
                workers_table.update_worker(worker_id, new_state)
                
                events.add_event(
                    datetime.now().strftime("%H:%M:%S"),
                    "RESUME",
                    f"{worker_id} → active"
                )
            except Exception as e:
                events.add_event(
                    datetime.now().strftime("%H:%M:%S"),
                    "ERROR",
                    f"Resume failed: {str(e)}"
                )
        else:
            # Demo mode
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "RESUME",
                f"{worker_id} → active (demo)"
            )
            
            # Update demo state
            if worker_id in self._workers_cache:
                self._workers_cache[worker_id]["state"] = "idle"
            workers_table.update_worker(worker_id, "idle")
    
    def action_inspect_worker(self) -> None:
        """Navigate to worker detail screen."""
        workers_table = self.query_one("#worker-table", WorkerTable)
        
        # Get selected worker
        cursor_row = workers_table.cursor_coordinate.row if workers_table.cursor_coordinate else None
        if cursor_row is None:
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "ERROR",
                "No worker selected for inspection"
            )
            return
        
        # Get worker ID from cache (ordered by row)
        worker_ids = list(self._workers_cache.keys())
        if cursor_row >= len(worker_ids):
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "ERROR",
                "Invalid worker selection"
            )
            return
        
        worker_id = worker_ids[cursor_row]
        
        # Use enhanced client if available
        client = self._enhanced_client._http if self._enhanced_client else self._api_client
        
        # Push worker detail screen
        self.app.push_screen(
            WorkerDetailScreen(
                worker_id=worker_id,
                api_client=client,
                theme=self.theme
            )
        )
    
    def on_screen_resume(self) -> None:
        """Refresh data when returning from a sub-screen."""
        # Trigger immediate refresh after returning from worker detail screen
        if self._http_connected:
            asyncio.create_task(self._http_poll_fallback())
        elif self._ws_connected and self._enhanced_client:
            # WebSocket connected - request initial state
            asyncio.create_task(self._enhanced_client.request_initial_state())
    
    def action_toggle_logs(self) -> None:
        """Toggle log visibility."""
        events = self.query_one("#event-stream", EventStream)
        events.toggle_class("hidden")
    
    def action_quit(self) -> None:
        """Quit the app."""
        self.app.exit()