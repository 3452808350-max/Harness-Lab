"""
Worker table for Control Plane TUI.

Shows status of all connected workers.
"""

from textual.widgets import DataTable
from textual.app import ComposeResult
from textual.coordinate import Coordinate

from ..theme import ColorTheme


class WorkerTable(DataTable):
    """Table showing worker status.
    
    Columns:
    - ID: Worker ID (e.g., w-01)
    - State: Worker state (idle, executing, draining, offline)
    - Role: Worker role profile (executor, general, reviewer, planner)
    - Task: Current task ID (or "-" if idle)
    - Lease: Lease remaining time (or "-" if not leased)
    """
    
    DEFAULT_CSS = """
    WorkerTable {
        width: auto;
        height: auto;
        padding: 1;
    }
    """
    
    def __init__(
        self,
        theme: ColorTheme = None,
        **kwargs
    ):
        super().__init__(**kwargs)
        self.theme = theme or ColorTheme.dark()
        self.worker_data = {}
    
    def on_mount(self) -> None:
        """Initialize table on mount."""
        # Add columns
        self.add_columns("ID", "State", "Role", "Task", "Lease")
        
        # Set table properties
        self.show_header = True
        self.zebra_stripes = True
    
    def add_worker(self, worker_id: str, state: str, role: str, task: str = "-", lease: str = "-") -> None:
        """Add a worker row."""
        self.worker_data[worker_id] = {
            "state": state,
            "role": role,
            "task": task,
            "lease": lease,
        }
        self.add_row(
            worker_id,
            self._styled_state(state),
            role,
            task,
            lease,
            key=worker_id,
        )
    
    def update_worker(self, worker_id: str, state: str = None, task: str = None, lease: str = None) -> None:
        """Update worker row."""
        if worker_id not in self.worker_data:
            return
        
        data = self.worker_data[worker_id]
        if state:
            data["state"] = state
        if task:
            data["task"] = task
        if lease:
            data["lease"] = lease
        
        # Find row index
        try:
            row_key = worker_id
            self.update_cell(row_key, "State", self._styled_state(data["state"]))
            self.update_cell(row_key, "Task", data["task"])
            self.update_cell(row_key, "Lease", data["lease"])
        except Exception:
            pass  # Row might not exist yet
    
    def remove_worker(self, worker_id: str) -> None:
        """Remove worker row."""
        if worker_id in self.worker_data:
            del self.worker_data[worker_id]
            self.remove_row(worker_id)
    
    def clear_workers(self) -> None:
        """Clear all worker rows."""
        self.worker_data.clear()
        self.clear()
    
    def _styled_state(self, state: str) -> str:
        """Get styled state string."""
        # State color mapping as specified
        icons = {
            "idle": "🟢",
            "leased": "🟡",
            "executing": "🟡",
            "draining": "🟡",
            "offline": "⚫",
            "unhealthy": "🔴",
            "registering": "🔵",
        }
        icon = icons.get(state.lower(), "⚪")
        return f"{icon} {state}"