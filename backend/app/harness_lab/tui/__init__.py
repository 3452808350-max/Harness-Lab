"""
Harness Lab TUI - Terminal User Interface for visual management.

Provides:
- Control Plane TUI: System monitoring, Worker status, Task queue
- Worker TUI: Local status, Task execution, Logs
- WebSocket Client: Real-time event updates
"""

from .app import HarnessLabTUI
from .theme import ColorTheme
from .api_client import APIConfig, ControlPlaneClient, create_client
from .ws_client import (
    WSConfig,
    ConnectionState,
    TUIWebSocketClient,
    MockWebSocketClient,
    create_ws_client,
)
from .ws_worker import (
    WebSocketWorker,
    TypedWebSocketWorker,
    WSMessage,
    WSConnectionState,
    WSHeartbeat,
    # Typed messages
    WSWorkerJoined,
    WSWorkerLeft,
    WSWorkerStateChanged,
    WSTaskDispatched,
    WSTaskCompleted,
    WSHealthUpdate,
    WSQueueUpdate,
    WSInitialState,
)
from .enhanced_client import (
    HybridConfig,
    EnhancedAPIClient,
    create_enhanced_client,
)

__all__ = [
    # App
    "HarnessLabTUI",
    "ColorTheme",
    # HTTP Client
    "APIConfig",
    "ControlPlaneClient",
    "create_client",
    # WebSocket Client
    "WSConfig",
    "ConnectionState",
    "TUIWebSocketClient",
    "MockWebSocketClient",
    "create_ws_client",
    # WebSocket Worker
    "WebSocketWorker",
    "TypedWebSocketWorker",
    "WSMessage",
    "WSConnectionState",
    "WSHeartbeat",
    "WSWorkerJoined",
    "WSWorkerLeft",
    "WSWorkerStateChanged",
    "WSTaskDispatched",
    "WSTaskCompleted",
    "WSHealthUpdate",
    "WSQueueUpdate",
    "WSInitialState",
    # Enhanced Client
    "HybridConfig",
    "EnhancedAPIClient",
    "create_enhanced_client",
]