"""Explicit Eufy HomeBase to Home Assistant alarm mappings."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class AlarmState(StrEnum):
    """HA alarm states used by the verified Eufy modes."""

    DISARMED = "disarmed"
    ARMED_HOME = "armed_home"
    ARMED_AWAY = "armed_away"
    ARMED_CUSTOM_BYPASS = "armed_custom_bypass"
    ARMED_NIGHT = "armed_night"
    ARMED_VACATION = "armed_vacation"
    PENDING = "pending"
    TRIGGERED = "triggered"


MODE_AWAY = 0
MODE_HOME = 1
MODE_CUSTOM_1 = 3
MODE_CUSTOM_2 = 4
MODE_CUSTOM_3 = 5
MODE_DISARMED = 63

# Custom 1/2/3 retain the established old-integration compatibility mapping.
RAW_TO_ALARM_STATE = {
    MODE_AWAY: AlarmState.ARMED_AWAY,
    MODE_HOME: AlarmState.ARMED_HOME,
    MODE_CUSTOM_1: AlarmState.ARMED_CUSTOM_BYPASS,
    MODE_CUSTOM_2: AlarmState.ARMED_NIGHT,
    MODE_CUSTOM_3: AlarmState.ARMED_VACATION,
    MODE_DISARMED: AlarmState.DISARMED,
}


def _as_int(raw: Any) -> int | None:
    """Read a wire integer that may arrive as int or numeric string (never a bool)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def alarm_state_for_raw(raw: Any) -> AlarmState | None:
    """Map only explicitly supported Eufy modes."""
    value = _as_int(raw)
    return None if value is None else RAW_TO_ALARM_STATE.get(value)


# The hub reports the whole alarm lifecycle on ONE push event (`alarm`), and the
# bridge's `phase` is static ("triggered" / "delayed") — what tells a start from a
# stop is the CusPushAlarmType code in `type`. These codes END an alarm; every other
# one names what STARTED it (PIR, camera, app, keypad panic, …). Verified live on a
# T8010 (`type: 4` on trigger, `type: 16` on stop) and a T8030 — mega-yfue/eufy-sdk#223.
ALARM_STOP_TYPES: frozenset[int] = frozenset(
    {
        0,  # HUB_STOP
        1,  # DEV_STOP
        15,  # HUB_STOP_BY_KEYPAD
        16,  # HUB_STOP_BY_APP
        17,  # HUB_STOP_BY_HUB
    }
)

PHASE_TRIGGERED = "triggered"
PHASE_DELAYED = "delayed"
PHASE_STOPPED = "stopped"


def alarm_phase_for_event(event: dict[str, Any]) -> str | None:
    """
    Classify a bridge `alarm` event as `stopped`, `delayed` or `triggered`.

    `delayed` is the entry/exit countdown. None for anything that isn't an alarm
    event. An event with no usable `type` is read as the bridge's own `phase`, so a
    start is never mistaken for a stop by default.
    """
    if event.get("event") != "alarm":
        return None
    code = _as_int(event.get("type"))
    if code is not None and code in ALARM_STOP_TYPES:
        return PHASE_STOPPED
    return PHASE_DELAYED if event.get("phase") == PHASE_DELAYED else PHASE_TRIGGERED


def panel_state_for(state: dict[str, Any]) -> AlarmState | None:
    """
    Derive the alarm panel's state from a station's live state map.

    A sounding alarm wins over the arming mode, then a running entry/exit delay,
    then the plain mode mapping.
    """
    if state.get("alarmTriggered"):
        return AlarmState.TRIGGERED
    if state.get("alarmPending"):
        return AlarmState.PENDING
    return alarm_state_for_raw(state.get("armingMode"))
