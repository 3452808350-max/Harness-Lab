"""
Worker Detail Screen - Detailed worker management view.

Shows comprehensive worker details and allows drain/resume operations.

WebSocket Integration:
- Receives real-time state changes via WebSocket events from Dashboard
- Falls back to HTTP polling when WebSocket disconnected
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Header,
    Static,
    DataTable,
    Collapsible,
)

from ..api_client import ControlPlaneClient, APIConfig
from ..theme import ColorTheme
from ..widgets import StatusBar
from ..ws_client import ConnectionState
from ..ws_worker import WSWorkerStateChanged, WSTaskDispatched, WSTaskCompleted


# State color mapping with emoji indicators
STATE_COLORS = {
    "idle": "🟢",
    "leased": "🟡",
    "executing": "🟡",
    "draining": "🟡",
    "offline": "⚫",
    "unhealthy": "🔴",
    "registering": "🟢",
}


class InfoSection(Static):
    """A section of worker information displayed as key-value pairs."""

    DEFAULT_CSS = """
    InfoSection {
        margin: 0 1;
        padding: 0 1;
        height: auto;
    }

    InfoSection .section-title {
        color: $primary;
        text-style: bold;
        margin-bottom: 1;
    }

    InfoSection .info-row {
        margin-bottom: 0;
    }

    InfoSection .info-key {
        color: $secondary;
    }

    InfoSection .info-value {
        color: $text;
    }
    """

    def __init__(self, title: str, data: Dict[str, Any] = None, **kwargs):
        super().__init__(**kwargs)
        self.section_title = title
        self.data = data or {}

    def update_data(self, data: Dict[str, Any]) -> None:
        """Update the displayed data."""
        self.data = data
        self._update_content()

    def _format_value(self, key: str, value: Any) -> str:
        """Format a value for display."""
        if value is None:
            return "[dim]-[/dim]"
        
        # Format timestamps
        if "at" in key.lower() or "time" in key.lower() or key.endswith("_at"):
            if isinstance(value, (int, float)):
                try:
                    dt = datetime.fromtimestamp(value)
                    return dt.strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError):
                    return str(value)
            return str(value)
        
        # Format lists
        if isinstance(value, list):
            if len(value) == 0:
                return "[dim]none[/dim]"
            return ", ".join(str(v) for v in value[:5]) + ("..." if len(value) > 5 else "")
        
        # Format dicts
        if isinstance(value, dict):
            if len(value) == 0:
                return "[dim]{}[/dim]"
            return str(value)[:50] + ("..." if len(str(value)) > 50 else "")
        
        # Format booleans
        if isinstance(value, bool):
            return "[green]✓[/green]" if value else "[red]✗[/red]"
        
        # Format numbers
        if isinstance(value, (int, float)):
            return str(value)
        
        return str(value) if value else "[dim]-[/dim]"

    def _update_content(self) -> None:
        """Update the content based on current data."""
        lines = [f"[bold]{self.section_title}[/bold]"]
        
        for key, value in self.data.items():
            # Pretty key names
            display_key = key.replace("_", " ").title()
            formatted_value = self._format_value(key, value)
            lines.append(f"  [cyan]{display_key}:[/cyan] {formatted_value}")
        
        self.update("\n".join(lines))

    def on_mount(self) -> None:
        """Initialize content on mount."""
        self._update_content()


class WorkerDetailScreen(Screen):
    """Detailed worker information screen.

    Shows comprehensive worker details organized into sections:
    1. Basic Info - label, hostname, pid, version, role, class, mode
    2. Status Info - state, drain_state, heartbeat_at, active_leases
    3. Execution Info - current_run_id, current_task_node_id, current_lease_id
    4. Sandbox Info - sandbox_backend, sandbox_ready, sandbox_stats
    5. Capabilities & Labels - capabilities, labels, eligible_labels
    6. History Info - lease_count, last_error, recent_leases
    7. Recent Leases Table - DataTable displaying recent leases

    Bindings:
        escape: Back to Dashboard
        d: Drain worker
        r: Resume worker
        e: Show events screen
        l: Toggle leases table
        h: Request heartbeat
    """

    DEFAULT_CSS = """
    WorkerDetailScreen {
        layout: vertical;
    }

    WorkerDetailScreen > Header {
        height: 1;
    }

    WorkerDetailScreen > Container {
        height: 1fr;
        overflow-y: auto;
    }

    WorkerDetailScreen > StatusBar {
        height: 1;
    }

    WorkerDetailScreen .worker-header {
        text-align: center;
        padding: 1;
        background: $surface;
    }

    WorkerDetailScreen .worker-title {
        text-style: bold;
        color: $primary;
    }

    WorkerDetailScreen .worker-state {
        margin-left: 2;
    }

    WorkerDetailScreen .section-grid {
        layout: grid;
        grid-size: 2;
        grid-columns: 1fr 1fr;
        grid-gutter: 1;
    }

    WorkerDetailScreen .leases-section {
        margin: 1;
        padding: 0;
        height: auto;
    }

    WorkerDetailScreen #leases-table {
        height: auto;
        max-height: 12;
    }

    WorkerDetailScreen .collapsed {
        display: none;
    }
    """

    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("d", "drain_worker", "Drain"),
        Binding("r", "resume_worker", "Resume"),
        Binding("e", "show_events", "Events"),
        Binding("l", "toggle_leases", "Leases"),
        Binding("h", "heartbeat", "Heartbeat"),
    ]

    # Reactive state
    worker_data: reactive[Dict[str, Any]] = reactive({})
    loading: reactive[bool] = reactive(True)
    error_message: reactive[str] = reactive("")
    leases_visible: reactive[bool] = reactive(True)

    def __init__(
        self,
        worker_id: str,
        theme: ColorTheme = None,
        api_client: ControlPlaneClient = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.worker_id = worker_id
        self.theme = theme or ColorTheme.dark()
        self._api_client = api_client
        self._poll_interval = 3.0  # Poll every 3 seconds
        self._poll_timer = None

    def compose(self) -> ComposeResult:
        """Compose worker detail screen layout."""
        yield Header()

        with Container(id="main-container"):
            with VerticalScroll(id="scroll-area"):
                # Worker header
                with Container(classes="worker-header"):
                    yield Static(self._get_header_text(), id="worker-title")
                    yield Static("Loading...", id="worker-state")

                # Section grid for info panels
                with Container(classes="section-grid"):
                    # Basic Info Section
                    with Vertical(classes="info-column"):
                        yield InfoSection("📋 Basic Info", {}, id="basic-info")

                    # Status Info Section
                    with Vertical(classes="info-column"):
                        yield InfoSection("🔴 Status", {}, id="status-info")

                    # Execution Info Section
                    with Vertical(classes="info-column"):
                        yield InfoSection("⚙️ Execution", {}, id="execution-info")

                    # Sandbox Info Section
                    with Vertical(classes="info-column"):
                        yield InfoSection("🐳 Sandbox", {}, id="sandbox-info")

                    # Capabilities Section
                    with Vertical(classes="info-column"):
                        yield InfoSection("🔧 Capabilities", {}, id="capabilities-info")

                    # History Section
                    with Vertical(classes="info-column"):
                        yield InfoSection("📜 History", {}, id="history-info")

                # Recent Leases Section
                with Container(classes="leases-section"):
                    yield Collapsible(
                        DataTable(id="leases-table", show_header=True, zebra_stripes=True),
                        title="📊 Recent Leases",
                        id="leases-collapsible",
                        collapsed=False,
                    )

        yield StatusBar(self.theme)

    def _get_header_text(self) -> str:
        """Get the header text with worker ID."""
        return f"Worker: {self.worker_id}"

    def on_mount(self) -> None:
        """Initialize the screen on mount."""
        header = self.query_one(Header)
        header.title = f"Worker: {self.worker_id}"

        # Initialize leases table columns
        leases_table = self.query_one("#leases-table", DataTable)
        leases_table.add_columns("Lease ID", "Task ID", "State", "Started", "Duration")

        # Start polling
        self._start_polling()

    def on_unmount(self) -> None:
        """Clean up on unmount."""
        self._stop_polling()

    def _start_polling(self) -> None:
        """Start polling for worker updates."""
        self._poll_timer = self.set_interval(self._poll_interval, self._poll_worker)

    def _stop_polling(self) -> None:
        """Stop polling."""
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None

    async def _poll_worker(self) -> None:
        """Poll the API for worker data."""
        if not self._api_client or not self._api_client.is_connected:
            # Try to use demo data or skip
            if not hasattr(self, '_demo_mode'):
                self._demo_mode = True
                self._load_demo_data()
            return

        try:
            data = await self._api_client.get_worker(self.worker_id)
            self.worker_data = data
            self._update_display(data)
            self.loading = False
            self.error_message = ""
        except Exception as e:
            self.error_message = str(e)
            self.loading = False
            # Show error in status
            state_widget = self.query_one("#worker-state", Static)
            state_widget.update(f"[red]Error: {e}[/red]")

    def _load_demo_data(self) -> None:
        """Load demo data for testing."""
        demo_data = {
            "worker_id": self.worker_id,
            "label": f"worker-{self.worker_id}",
            "hostname": "host-01.internal",
            "pid": 12345,
            "version": "1.2.3",
            "role": "executor",
            "class": "standard",
            "mode": "auto",
            "state": "idle",
            "drain_state": None,
            "heartbeat_at": datetime.now().timestamp(),
            "active_leases": 0,
            "current_run_id": None,
            "current_task_node_id": None,
            "current_lease_id": None,
            "sandbox_backend": "docker",
            "sandbox_ready": True,
            "sandbox_stats": {
                "containers_running": 2,
                "memory_used_mb": 512,
                "disk_used_gb": 1.2,
            },
            "capabilities": ["code-execution", "docker", "network-access"],
            "labels": {
                "env": "production",
                "region": "us-west-2",
            },
            "eligible_labels": ["high-memory", "gpu-available"],
            "lease_count": 42,
            "last_error": None,
            "recent_leases": [
                {
                    "lease_id": "lease-001",
                    "task_id": "task-42",
                    "state": "completed",
                    "started_at": datetime.now().timestamp() - 3600,
                    "duration_seconds": 120,
                },
                {
                    "lease_id": "lease-002",
                    "task_id": "task-41",
                    "state": "completed",
                    "started_at": datetime.now().timestamp() - 7200,
                    "duration_seconds": 95,
                },
            ],
        }
        self.worker_data = demo_data
        self._update_display(demo_data)

    def _update_display(self, data: Dict[str, Any]) -> None:
        """Update all display sections with worker data."""
        # Update header with state
        state_widget = self.query_one("#worker-state", Static)
        state = data.get("state", "unknown")
        state_icon = STATE_COLORS.get(state, "⚪")
        state_widget.update(f"{state_icon} [bold]{state.upper()}[/bold]")

        # Update Basic Info
        basic_info = self.query_one("#basic-info", InfoSection)
        basic_info.update_data({
            "label": data.get("label"),
            "hostname": data.get("hostname"),
            "pid": data.get("pid"),
            "version": data.get("version"),
            "role": data.get("role"),
            "class": data.get("worker_class", data.get("class")),
            "mode": data.get("mode"),
        })

        # Update Status Info
        status_info = self.query_one("#status-info", InfoSection)
        status_info.update_data({
            "state": data.get("state"),
            "drain_state": data.get("drain_state"),
            "heartbeat_at": data.get("heartbeat_at"),
            "active_leases": data.get("active_leases"),
        })

        # Update Execution Info
        execution_info = self.query_one("#execution-info", InfoSection)
        execution_info.update_data({
            "current_run_id": data.get("current_run_id"),
            "current_task_node_id": data.get("current_task_node_id"),
            "current_lease_id": data.get("current_lease_id"),
        })

        # Update Sandbox Info
        sandbox_info = self.query_one("#sandbox-info", InfoSection)
        sandbox_stats = data.get("sandbox_stats", {})
        sandbox_info.update_data({
            "sandbox_backend": data.get("sandbox_backend"),
            "sandbox_ready": data.get("sandbox_ready"),
            "containers_running": sandbox_stats.get("containers_running", 0),
            "memory_used_mb": sandbox_stats.get("memory_used_mb", 0),
            "disk_used_gb": sandbox_stats.get("disk_used_gb", 0),
        })

        # Update Capabilities Info
        capabilities_info = self.query_one("#capabilities-info", InfoSection)
        capabilities = data.get("capabilities", [])
        labels = data.get("labels", {})
        eligible_labels = data.get("eligible_labels", [])

        capabilities_info.update_data({
            "capabilities": capabilities,
            "labels": labels,
            "eligible_labels": eligible_labels,
        })

        # Update History Info
        history_info = self.query_one("#history-info", InfoSection)
        history_info.update_data({
            "lease_count": data.get("lease_count", 0),
            "last_error": data.get("last_error"),
        })

        # Update Recent Leases Table
        self._update_leases_table(data.get("recent_leases", []))

    def _update_leases_table(self, leases: List[Dict[str, Any]]) -> None:
        """Update the recent leases table."""
        table = self.query_one("#leases-table", DataTable)
        table.clear()

        for lease in leases[:10]:  # Show last 10 leases
            lease_id = lease.get("lease_id", "-")
            task_id = lease.get("task_id", "-")
            state = lease.get("state", "-")
            
            # Format started time
            started_at = lease.get("started_at")
            if started_at:
                try:
                    dt = datetime.fromtimestamp(started_at)
                    started = dt.strftime("%H:%M:%S")
                except (ValueError, OSError, TypeError):
                    started = "-"
            else:
                started = "-"
            
            # Format duration
            duration_sec = lease.get("duration_seconds")
            if duration_sec:
                duration = f"{duration_sec}s"
            else:
                duration = "-"
            
            # Style state
            state_icon = STATE_COLORS.get(state, "⚪")
            styled_state = f"{state_icon} {state}"
            
            table.add_row(lease_id, task_id, styled_state, started, duration)

    # === Action Handlers ===

    def action_back(self) -> None:
        """Go back to dashboard."""
        self.app.pop_screen()

    async def action_drain_worker(self) -> None:
        """Drain this worker - stop accepting new tasks."""
        if not self._api_client or not self._api_client.is_connected:
            self._show_notification("[yellow]Demo mode: Drain action simulated[/yellow]")
            return

        try:
            result = await self._api_client.drain_worker(
                self.worker_id, 
                reason="Manual drain via TUI"
            )
            new_state = result.get("state", "draining")
            self._show_notification(f"[green]Worker draining: {new_state}[/green]")
            
            # Invalidate cache and refresh
            self._api_client.invalidate_worker_cache(self.worker_id)
            await self._poll_worker()
        except Exception as e:
            self._show_notification(f"[red]Drain failed: {e}[/red]")

    async def action_resume_worker(self) -> None:
        """Resume this worker - start accepting tasks again."""
        if not self._api_client or not self._api_client.is_connected:
            self._show_notification("[yellow]Demo mode: Resume action simulated[/yellow]")
            return

        try:
            result = await self._api_client.resume_worker(self.worker_id)
            new_state = result.get("state", "idle")
            self._show_notification(f"[green]Worker resumed: {new_state}[/green]")
            
            # Invalidate cache and refresh
            self._api_client.invalidate_worker_cache(self.worker_id)
            await self._poll_worker()
        except Exception as e:
            self._show_notification(f"[red]Resume failed: {e}[/red]")

    def action_show_events(self) -> None:
        """Switch to Events/Logs screen."""
        self.app.push_screen("logs")

    def action_toggle_leases(self) -> None:
        """Toggle leases table visibility."""
        try:
            collapsible = self.query_one("#leases-collapsible", Collapsible)
            collapsible.collapsed = not collapsible.collapsed
            self.leases_visible = not collapsible.collapsed
            status = "visible" if self.leases_visible else "hidden"
            self._show_notification(f"[cyan]Leases table {status}[/cyan]")
        except Exception:
            pass

    async def action_heartbeat(self) -> None:
        """Request heartbeat from worker."""
        if not self._api_client or not self._api_client.is_connected:
            self._show_notification("[yellow]Demo mode: Heartbeat requested[/yellow]")
            return

        try:
            # Force refresh to get latest heartbeat time
            self._api_client.invalidate_worker_cache(self.worker_id)
            await self._poll_worker()
            self._show_notification("[green]Worker data refreshed[/green]")
        except Exception as e:
            self._show_notification(f"[red]Heartbeat check failed: {e}[/red]")

    def _show_notification(self, message: str) -> None:
        """Show a notification message in the state widget."""
        try:
            state_widget = self.query_one("#worker-state", Static)
            current_state = self.worker_data.get("state", "unknown")
            state_icon = STATE_COLORS.get(current_state, "⚪")
            
            # Temporarily show notification
            state_widget.update(f"{state_icon} [bold]{current_state.upper()}[/bold] | {message}")
            
            # Reset after 3 seconds
            self.set_timer(3.0, lambda: self._reset_state_display())
        except Exception:
            pass

    def _reset_state_display(self) -> None:
        """Reset state display to current state."""
        try:
            state_widget = self.query_one("#worker-state", Static)
            state = self.worker_data.get("state", "unknown")
            state_icon = STATE_COLORS.get(state, "⚪")
            state_widget.update(f"{state_icon} [bold]{state.upper()}[/bold]")
        except Exception:
            pass


# Backwards compatibility alias
WorkersScreen = WorkerDetailScreen