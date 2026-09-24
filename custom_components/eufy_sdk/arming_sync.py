"""Realtime synchronization for HomeBase arming mode events."""

from __future__ import annotations

from typing import Any


def apply_arming_mode_event(coordinator: Any, event: dict[str, Any]) -> bool:
    """
    Apply a valued armingModeChanged event and notify entity listeners.

    A MODE_SWITCH push carries two numbers: `arming`, the mode the hub was SET to, and
    `mode`, the one it is now ENFORCING (they differ under `schedule` / `geo`). Both
    are stored — `armingMode` and `currentMode` — so the panel keeps showing the set
    mode and the Current mode sensor the enforced one. A push with only `mode` (the
    shape the integration read before `arming` was known) still updates `armingMode`,
    as it always did.
    """
    if event.get("event") != "armingModeChanged" or "mode" not in event:
        return False
    serial = event.get("deviceSn") or event.get("sn")
    if not serial or serial not in coordinator.data:
        return False
    state = coordinator.data[serial].setdefault("state", {})
    state["armingMode"] = event["arming"] if "arming" in event else event["mode"]
    state["currentMode"] = event["mode"]
    coordinator.async_update_listeners()
    return True
