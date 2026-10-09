"""Realtime synchronization for the HomeBase alarm lifecycle (`alarm` push events)."""

from __future__ import annotations

from typing import Any

from .alarm_logic import AlarmState, alarm_phase_for_event, alarm_state_for_raw

# The lifecycle lives in its own per-station map (`runtime_data.station_alarms`),
# NOT in the device's polled `state`: the coordinator rebuilds `coordinator.data`
# from the bridge on every refresh, which would flip a sounding alarm back to the
# arming mode mid-alarm. Only a stop push, a disarm/home, or the fallback clears it.
type StationAlarms = dict[str, dict[str, Any]]


def alarm_event_serial(event: dict[str, Any]) -> str | None:
    """Return the station an `alarm` event belongs to (the hub's own serial)."""
    return event.get("deviceSn") or event.get("stationSn") or event.get("sn")


def alarm_station_serial(coordinator: Any, event: dict[str, Any]) -> str | None:
    """
    Return the station whose alarm an `alarm` push reports.

    When a camera attached to a HomeBase trips the alarm, the push names the CAMERA
    in `deviceSn`/`s` (seen live: a T8113 on a T8030 in Away), and the hub only
    appears inside `rec_content[].station_sn`. The lifecycle belongs to the station —
    its panel and Alarm sensor — so resolve the camera to its station: first from the
    device's own `stationSn` in the device list, else from the push's record.
    """
    serial = alarm_event_serial(event)
    if not serial:
        return None
    station = (coordinator.data.get(serial) or {}).get("stationSn")
    if not station or station == serial:
        for record in event.get("rec_content") or []:
            if isinstance(record, dict) and record.get("station_sn"):
                station = record["station_sn"]
                break
    if station and station != serial and station in coordinator.data:
        return station
    return serial


def apply_alarm_event(
    coordinator: Any, alarms: StationAlarms, event: dict[str, Any]
) -> str | None:
    """
    Fold one `alarm` event into the station's alarm lifecycle and notify listeners.

    Returns the phase that was applied (`triggered` / `delayed` / `stopped`), or
    None when the event is not an alarm or names a device the coordinator doesn't
    know.
    """
    phase = alarm_phase_for_event(event)
    if phase is None:
        return None
    serial = alarm_station_serial(coordinator, event)
    if not serial or serial not in coordinator.data:
        return None
    alarms[serial] = {
        "alarmTriggered": phase == "triggered",
        "alarmPending": phase == "delayed",
        # Who/what started or ended it, for the entity's attributes: the
        # CusPushAlarmType code and, on a stop from the app, the account that sent it.
        "alarmType": event.get("type"),
        "alarmUser": event.get("user_name"),
    }
    coordinator.async_update_listeners()
    return phase


def clear_alarm(coordinator: Any, alarms: StationAlarms, serial: str) -> bool:
    """Drop a station's alarm flags (the fallback when no stop push ever arrives)."""
    alarm = alarms.get(serial)
    if not alarm or not (alarm.get("alarmTriggered") or alarm.get("alarmPending")):
        return False
    alarm["alarmTriggered"] = False
    alarm["alarmPending"] = False
    coordinator.async_update_listeners()
    return True


def clear_pending(coordinator: Any, alarms: StationAlarms, serial: str) -> bool:
    """
    End an entry/exit countdown the user cancelled by disarming (or going Home).

    The hub answers a disarm during the delay with a mode change, never an alarm
    stop, so without this the panel would stay `pending` until the fallback.
    """
    alarm = alarms.get(serial)
    if not alarm or not alarm.get("alarmPending"):
        return False
    alarm["alarmPending"] = False
    coordinator.async_update_listeners()
    return True


def end_cancelled_delay(coordinator: Any, alarms: StationAlarms, serial: str) -> bool:
    """End `pending` once the station's (fresh) arming mode is Disarmed or Home."""
    device = coordinator.data.get(serial)
    if not device:
        return False
    raw = device.get("state", {}).get("armingMode")
    if alarm_state_for_raw(raw) not in (AlarmState.DISARMED, AlarmState.ARMED_HOME):
        return False
    return clear_pending(coordinator, alarms, serial)
