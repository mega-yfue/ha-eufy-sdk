"""Realtime synchronization for the HomeBase alarm lifecycle (`alarm` push events)."""

from __future__ import annotations

from typing import Any

from .alarm_logic import alarm_phase_for_event


def alarm_event_serial(event: dict[str, Any]) -> str | None:
    """Return the station an `alarm` event belongs to (the hub's own serial)."""
    return event.get("deviceSn") or event.get("stationSn") or event.get("sn")


def apply_alarm_event(coordinator: Any, event: dict[str, Any]) -> str | None:
    """
    Fold one `alarm` event into the station's live state and notify listeners.

    Returns the phase that was applied (`triggered` / `delayed` / `stopped`), or
    None when the event is not an alarm or names a device the coordinator doesn't
    know.
    """
    phase = alarm_phase_for_event(event)
    if phase is None:
        return None
    serial = alarm_event_serial(event)
    if not serial or serial not in coordinator.data:
        return None
    state = coordinator.data[serial].setdefault("state", {})
    state["alarmTriggered"] = phase == "triggered"
    state["alarmPending"] = phase == "delayed"
    # Who/what started or ended it, for the entity's attributes: the CusPushAlarmType
    # code and, on a stop from the app, the account that sent it.
    state["alarmType"] = event.get("type")
    state["alarmUser"] = event.get("user_name")
    coordinator.async_update_listeners()
    return phase


def clear_alarm(coordinator: Any, serial: str) -> bool:
    """Drop a station's alarm flags (the fallback when no stop push ever arrives)."""
    device = coordinator.data.get(serial)
    if not device:
        return False
    state = device.setdefault("state", {})
    if not (state.get("alarmTriggered") or state.get("alarmPending")):
        return False
    state["alarmTriggered"] = False
    state["alarmPending"] = False
    coordinator.async_update_listeners()
    return True
