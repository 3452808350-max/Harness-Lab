"""
Dashboard screen - Main control plane TUI view.

Shows:
- Service status panel (left)
- Worker table (center/right)
- Event stream (bottom)
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

from ..widgets import ServicePanel, WorkerTable, StatusBar, EventStream
from ..theme import ColorTheme
from ..api_client import ControlPlaneClient, APIConfig


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
        **kwargs
    ):
        super().__init__(**kwargs)
        self.theme = theme or ColorTheme.dark()
        self.api_url = api_url
        
        # API client
        self._api_config = APIConfig(base_url=api_url)
        self._api_client: Optional[ControlPlaneClient] = None
        
        # Worker state cache for tracking changes
        self._workers_cache: Dict[str, Dict] = {}
        
        # Connection state
        self._api_connected = False
        self._using_demo_data = False
    
    def compose(self) -> ComposeResult:
        """Compose dashboard layout."""
        yield Header(id="header")
        
        with Container(id="main"):
            with Horizontal(id="top-panels"):
                yield ServicePanel(self.theme, id="service-panel")
                yield WorkerTable(self.theme, id="worker-table")
            
            yield EventStream(self.theme, id="event-stream")
        
        yield StatusBar(self.theme, id="status-bar")
    
    def on_mount(self) -> None:
        """Initialize dashboard on mount."""
        # Set header title
        header = self.query_one("#header", Header)
        header.title = "Harness Lab Control Plane"
        
        # Initialize API client connection (async)
        self._init_api_connection()
        
        # Start polling for updates (every 2 seconds)
        self.set_interval(2.0, self._poll_status)
    
    def on_unmount(self) -> None:
        """Cleanup on unmount."""
        # Disconnect API client (async cleanup)
        if self._api_client:
            asyncio.create_task(self._api_client.disconnect())
    
    def _init_api_connection(self) -> None:
        """Initialize API client connection."""
        self._api_client = ControlPlaneClient(self._api_config)
        
        # Start async connection
        asyncio.create_task(self._connect_api())
    
    async def _connect_api(self) -> None:
        """Connect to API asynchronously."""
        try:
            connected = await self._api_client.connect()
            if connected:
                self._api_connected = True
                self._using_demo_data = False
                
                # Log successful connection
                events = self.query_one("#event-stream", EventStream)
                events.add_event(
                    datetime.now().strftime("%H:%M:%S"),
                    "CONNECT",
                    f"API connected at {self.api_url}"
                )
                
                # Immediately fetch data
                await self._poll_status()
            else:
                self._api_connected = False
                self._using_demo_data = True
                
                # Log connection failure and use demo data
                events = self.query_one("#event-stream", EventStream)
                error = self._api_client.last_error or "Unknown error"
                events.add_event(
                    datetime.now().strftime("%H:%M:%S"),
                    "INFO",
                    "Using demo data (API unavailable)"
                )
                
                # Initialize with demo data as fallback
                self._init_demo_data()
        except Exception as e:
            self._api_connected = False
            self._using_demo_data = True
            
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "ERROR",
                f"API init error: {str(e)}"
            )
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "INFO",
                "Using demo data fallback"
            )
            
            # Initialize with demo data as fallback
            self._init_demo_data()
    
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
    
    async def _poll_status(self) -> None:
        """Poll control plane API for status updates.
        
        When connected:
        - Fetch health and workers data in parallel
        - Update ServicePanel, WorkerTable, StatusBar
        - Handle connection errors gracefully
        """
        if not self._api_connected or not self._api_client:
            # Use demo simulation when not connected
            await self._poll_demo()
            return
        
        try:
            # Fetch health and workers in parallel
            health_task = asyncio.create_task(self._api_client.get_health())
            workers_task = asyncio.create_task(self._api_client.list_workers())
            
            health_data, workers_data = await asyncio.gather(
                health_task, workers_task, return_exceptions=True
            )
            
            # Handle health data
            if isinstance(health_data, Exception):
                events = self.query_one("#event-stream", EventStream)
                events.add_event(
                    datetime.now().strftime("%H:%M:%S"),
                    "ERROR",
                    f"Health check failed: {str(health_data)}"
                )
            else:
                self._update_services(health_data)
            
            # Handle workers data
            if isinstance(workers_data, Exception):
                events = self.query_one("#event-stream", EventStream)
                events.add_event(
                    datetime.now().strftime("%H:%M:%S"),
                    "ERROR",
                    f"Workers fetch failed: {str(workers_data)}"
                )
            else:
                self._sync_workers(workers_data)
            
        except Exception as e:
            events = self.query_one("#event-stream", EventStream)
            events.add_event(
                datetime.now().strftime("%H:%M:%S"),
                "ERROR",
                f"Poll error: {str(e)}"
            )
            
            # Check if connection lost
            if self._api_client and not self._api_client.is_connected:
                self._api_connected = False
                self._using_demo_data = True
                events.add_event(
                    datetime.now().strftime("%H:%M:%S"),
                    "ERROR",
                    "Connection lost, using demo data"
                )
    
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
    
    async def _poll_demo(self) -> None:
        """Demo mode polling simulation."""
        # For demo mode, just add simulated events
        events = self.query_one("#event-stream", EventStream)
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Simulate events
        import random
        event_types = ["HEARTBEAT", "DISPATCH"]
        workers = ["w-01", "w-02", "w-03"]
        worker = random.choice(workers)
        event_type = random.choice(event_types)
        
        if event_type == "HEARTBEAT":
            events.add_event(timestamp, "HEARTBEAT", f"{worker} (idle)")
        else:
            events.add_event(timestamp, "DISPATCH", f"T-{random.randint(50,100)} → {worker}")
    
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
        
        if self._api_connected and self._api_client:
            try:
                result = await self._api_client.drain_worker(worker_id, reason="Manual drain via TUI")
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
        
        if self._api_connected and self._api_client:
            try:
                result = await self._api_client.resume_worker(worker_id)
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
        """Inspect selected worker."""
        # TODO: Implement worker inspection screen
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
        
        # TODO: Push worker detail screen
        events = self.query_one("#event-stream", EventStream)
        events.add_event(
            datetime.now().strftime("%H:%M:%S"),
            "INFO",
            "Inspect worker (not implemented)"
        )
    
    def action_toggle_logs(self) -> None:
        """Toggle log visibility."""
        events = self.query_one("#event-stream", EventStream)
        events.toggle_class("hidden")
    
    def action_quit(self) -> None:
        """Quit the app."""
        self.app.exit()