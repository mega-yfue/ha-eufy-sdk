"""
The mode a HomeBase is actually IN, as opposed to the one it was SET to.

`armingMode` (param 1224) is the guard mode a user chose. Two of its values are not
modes at all but rules: `schedule` (2) hands the hub a weekly timetable and `geo` (47)
hands it to presence. The mode the hub then enforces — the one the eufy app shows as
"current mode" and the old integration exposed as `current_mode` — is a different
number, and it is what an automation or a dashboard usually wants.

How it is resolved, with no hub word to go on:

- `schedule`: `jsonSchedule` (param 1254) is in every cloud poll, so the current slot
  resolves it locally. Weekdays follow the hub's own convention, Sunday = 0 (the
  legacy client builds its schedule bitmask the same way: SUNDAY = 1 << 0).
- any other set mode IS the mode being enforced.

A MODE_SWITCH push is NOT used: the legacy client names an `arming` / `mode` pair on
it, but nothing on this stack has seen those fields. Every `armingModeChanged` logged
on a T8030 and a T9000 over a day, including across a timetable slot boundary, arrived
as `{event, deviceSn}` only, and the hub pushed nothing at the boundary itself. So the
timetable is the only source until a capture shows otherwise.

`geo` has no local answer: presence is the hub's to judge, so until a push says which
mode it chose the result is unknown, never `geo` itself (a rule, not a mode).

Time zone: the timetable is in the HomeBase's local time, and it is resolved here
against the `at` the caller passes, which the sensor takes from Home Assistant's
configured time zone (`dt_util.now()`). The two agree when HA's time zone matches the
hub's; if they differ (HA left on UTC, say) every slot resolves shifted by the offset.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


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


MODE_SCHEDULE = 2
MODE_GEO = 47

# The SDK's own labels for `armingMode`, so the sensor reads exactly like the select.
MODE_LABELS: dict[int, str] = {
    0: "away",
    1: "home",
    2: "schedule",
    3: "custom1",
    4: "custom2",
    5: "custom3",
    6: "off",
    47: "geo",
    63: "disarmed",
}

SOURCE_SCHEDULE = "schedule"
SOURCE_SET = "set"

# Minutes in a day; a slot written as ending 23:59 really runs to midnight.
_END_OF_DAY = 24 * 60


def mode_label(raw: Any) -> str | None:
    """Return the SDK label for a wire mode, or None for an unknown one."""
    value = _as_int(raw)
    return None if value is None else MODE_LABELS.get(value)


def _eufy_weekday(at: datetime) -> int:
    """Python counts Monday = 0; the hub's timetable counts Sunday = 0."""
    return (at.weekday() + 1) % 7


def _slots(schedule: Any) -> list[Any]:
    """Return the timetable's slots (`jsonSchedule.schedules`); [] if malformed."""
    if not isinstance(schedule, dict):
        return []
    slots = schedule.get("schedules")
    return slots if isinstance(slots, list) else []


def resolve_schedule(schedule: Any, at: datetime) -> int | None:
    """
    Return the `mode_id` the timetable prescribes at `at`, or None if no slot covers it.

    Slots are `{week, start_h, start_m, end_h, end_m, mode_id}`. A slot's end is the
    next slot's start, so it is exclusive — at 20:20 a hub whose slots meet there is
    already in the later one (watched live on a T8030) — except that a day's last slot
    is written as ending 23:59 and has to cover that minute too.
    """
    slots = _slots(schedule)
    if not slots:
        return None
    weekday = _eufy_weekday(at)
    minute = at.hour * 60 + at.minute
    for slot in slots:
        if not isinstance(slot, dict) or _as_int(slot.get("week")) != weekday:
            continue
        start = _as_int(slot.get("start_h"))
        end = _as_int(slot.get("end_h"))
        mode = _as_int(slot.get("mode_id"))
        if start is None or end is None or mode is None:
            continue
        start_min = start * 60 + (_as_int(slot.get("start_m")) or 0)
        end_min = end * 60 + (_as_int(slot.get("end_m")) or 0)
        if end_min == _END_OF_DAY - 1:
            end_min = _END_OF_DAY
        if start_min <= minute < end_min:
            return mode
    return None


def current_mode_for(state: dict[str, Any], at: datetime) -> tuple[int | None, str]:
    """
    Return the mode the hub is enforcing, and where that answer came from.

    A `schedule` set mode resolves through the timetable, `geo` stays unknown (only
    the hub can say what presence chose, and it doesn't), and any other set mode IS
    the current mode.
    """
    set_mode = _as_int(state.get("armingMode"))
    if set_mode == MODE_SCHEDULE:
        return resolve_schedule(state.get("jsonSchedule"), at), SOURCE_SCHEDULE
    if set_mode == MODE_GEO:
        return None, SOURCE_SET
    return set_mode, SOURCE_SET


def next_schedule_boundary(schedule: Any, at: datetime) -> datetime | None:
    """
    Return the next moment after `at` where a timetable slot starts or ends.

    That is when the resolved mode can change without the hub pushing anything, so
    the sensor re-evaluates there instead of waiting for the next poll. Looks up to a
    week ahead; None when the timetable has no usable slot.
    """
    slots = _slots(schedule)
    if not slots:
        return None
    day_start = at.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(8):
        day = day_start + timedelta(days=offset)
        weekday = _eufy_weekday(day)
        marks: set[int] = set()
        for slot in slots:
            if not isinstance(slot, dict) or _as_int(slot.get("week")) != weekday:
                continue
            start = _as_int(slot.get("start_h"))
            end = _as_int(slot.get("end_h"))
            if start is None or end is None:
                continue
            marks.add(start * 60 + (_as_int(slot.get("start_m")) or 0))
            end_min = end * 60 + (_as_int(slot.get("end_m")) or 0)
            marks.add(_END_OF_DAY if end_min == _END_OF_DAY - 1 else end_min)
        for minute in sorted(marks):
            moment = day + timedelta(minutes=minute)
            if moment > at:
                return moment
    return None


def current_mode_attributes(state: dict[str, Any], now: datetime) -> dict[str, Any]:
    """
    Return the set mode's label, the enforced mode id and which source answered.

    `source` is `schedule` when the timetable resolved the mode and `set` when the
    station is on a fixed mode, so "on Schedule, currently Home" reads apart from
    "set to Home".
    """
    mode, source = current_mode_for(state, now)
    return {
        "arming_mode": mode_label(state.get("armingMode")),
        "mode_id": mode,
        "source": source,
    }
