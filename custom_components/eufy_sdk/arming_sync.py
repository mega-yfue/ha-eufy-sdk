"""Realtime synchronization for HomeBase arming mode events."""

from __future__ import annotations

from typing import Any

# Who switched the mode, from the MODE_SWITCH push's `user` (eufy's CusPushMode).
# Seen live on a HomeBase 2 (T8010): 1 on a keypad switch, 2 on an app switch.
ARMING_SOURCES = {1: "keypad", 2: "app"}


def apply_arming_mode_event(
    coordinator: Any,
    event: dict[str, Any],
    changes: dict[str, dict[str, Any]] | None = None,
) -> bool:
    """
    Apply a valued armingModeChanged event and notify entity listeners.

    When the push names who switched the mode (`user_name`, `user`), that is
    recorded in `changes` ({sn: {changed_by, changed_via}}), kept outside the polled
    device state so a refresh does not drop it. A push without them leaves the
    last record in place.
    """
    if event.get("event") != "armingModeChanged" or "mode" not in event:
        return False
    serial = event.get("deviceSn") or event.get("sn")
    if not serial or serial not in (coordinator.data or {}):
        return False
    coordinator.data[serial].setdefault("state", {})["armingMode"] = event["mode"]
    if changes is not None and ("user_name" in event or "user" in event):
        user = event.get("user")
        changes[serial] = {
            "changed_by": event.get("user_name"),
            "changed_via": ARMING_SOURCES.get(user, user)
            if isinstance(user, int)
            else user,
        }
    coordinator.async_update_listeners()
    return True
