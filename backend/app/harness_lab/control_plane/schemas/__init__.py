"""WebSocket message schema definitions.

This module provides JSON Schema definitions for WebSocket message formats.
Schemas are stored as JSON files and can be used for:
- Message validation
- Client documentation
- Type generation
"""

from pathlib import Path

SCHEMA_DIR = Path(__file__).parent

SCHEMA_FILES = {
    "envelope": "ws-envelope.json",
    "worker": "ws-worker-payload.json",
    "lease": "ws-lease-payload.json",
    "queue": "ws-queue-payload.json",
    "health": "ws-health-payload.json",
}


def get_schema_path(schema_name: str) -> Path:
    """Get path to a schema file.
    
    Args:
        schema_name: Schema name (envelope, worker, lease, queue, health)
        
    Returns:
        Path to the schema JSON file
    """
    filename = SCHEMA_FILES.get(schema_name)
    if not filename:
        raise ValueError(f"Unknown schema: {schema_name}")
    return SCHEMA_DIR / filename


def load_schema(schema_name: str) -> dict:
    """Load a schema from JSON file.
    
    Args:
        schema_name: Schema name (envelope, worker, lease, queue, health)
        
    Returns:
        Schema as dict
    """
    import json
    
    path = get_schema_path(schema_name)
    with open(path) as f:
        return json.load(f)


__all__ = [
    "SCHEMA_DIR",
    "SCHEMA_FILES",
    "get_schema_path",
    "load_schema",
]