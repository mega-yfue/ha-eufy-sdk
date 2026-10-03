"""Realtime synchronization for generic device property changes."""

from __future__ import annotations

from typing import Any, Literal

PropertySyncResult = Literal["applied", "refresh"] | None


def apply_property_changed_event(
    coordinator: Any,
    event: dict[str, Any],
) -> PropertySyncResult:
    """
    Apply a bridge propertyChanged event to the coordinator.

    Events carrying a concrete value can update Home Assistant immediately.
    A valueless propertyChanged only says that the property moved, so the caller
    must re-read the device instead of inventing a value.
    """
    if event.get("event") != "propertyChanged":
        return None

    serial = event.get("deviceSn") or event.get("sn")
    prop = event.get("property")

    if not serial or serial not in coordinator.data:
        return None

    if not isinstance(prop, str) or not prop:
        return None

    if "value" not in event:
        return "refresh"

    coordinator.data[serial].setdefault("state", {})[prop] = event["value"]
    coordinator.async_update_listeners()
    return "applied"
