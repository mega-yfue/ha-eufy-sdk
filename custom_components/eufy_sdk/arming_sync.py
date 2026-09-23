"""Realtime synchronization for HomeBase arming mode events."""

from __future__ import annotations

from typing import Any


def apply_arming_mode_event(coordinator: Any, event: dict[str, Any]) -> bool:
    """Apply a valued armingModeChanged event and notify entity listeners."""
    if event.get("event") != "armingModeChanged" or "mode" not in event:
        return False
    serial = event.get("deviceSn") or event.get("sn")
    if not serial or serial not in coordinator.data:
        return False
    coordinator.data[serial].setdefault("state", {})["armingMode"] = event["mode"]
    coordinator.async_update_listeners()
    return True
