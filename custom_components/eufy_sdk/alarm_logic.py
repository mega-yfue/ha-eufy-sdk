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


def alarm_state_for_raw(raw: Any) -> AlarmState | None:
    """Map only explicitly supported Eufy modes."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        try:
            value = int(raw)
        except ValueError:
            return None
    else:
        return None
    return RAW_TO_ALARM_STATE.get(value)
