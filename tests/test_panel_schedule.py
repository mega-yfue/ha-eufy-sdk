# ruff: noqa: ANN201, ANN202, D100, D101, D102, INP001, PT009, SLF001

import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from custom_components.eufy_sdk import alarm_control_panel
from custom_components.eufy_sdk import entity as entity_module

STATION = "T8030P0000000001"
# Home (1) from 07:00 to 20:20, Away (0) from 20:20 to the end of the day, every day.
_SCHEDULE = {
    "schedules": [
        slot
        for w in range(7)
        for slot in (
            {
                "week": w,
                "start_h": 7,
                "start_m": 0,
                "end_h": 20,
                "end_m": 20,
                "mode_id": 1,
            },
            {
                "week": w,
                "start_h": 20,
                "start_m": 20,
                "end_h": 23,
                "end_m": 59,
                "mode_id": 0,
            },
        )
    ]
}


def _panel(state: dict, alarms: dict | None = None):
    coordinator = Mock()
    coordinator.data = {STATION: {"capabilities": ["arming"], "state": state}}
    coordinator.config_entry.runtime_data.station_alarms = alarms or {}
    entity = alarm_control_panel.EufySdkAlarmControlPanel(coordinator, STATION)
    entity.hass = Mock()
    entity.async_write_ha_state = Mock()
    return entity


def _at(hour: int, minute: int):
    return patch.object(
        alarm_control_panel.dt_util,
        "now",
        return_value=datetime(2026, 9, 24, hour, minute),  # noqa: DTZ001
    )


class PanelScheduleTests(unittest.TestCase):
    def test_on_schedule_the_panel_shows_the_mode_the_timetable_enforces(self):
        entity = _panel({"armingMode": 2, "jsonSchedule": _SCHEDULE})
        with _at(12, 0):
            self.assertEqual(entity.alarm_state, "armed_home")
        with _at(21, 0):
            self.assertEqual(entity.alarm_state, "armed_away")

    def test_on_geo_the_panel_stays_unknown(self):
        with _at(12, 0):
            self.assertIsNone(_panel({"armingMode": 47}).alarm_state)

    def test_a_set_mode_and_a_sounding_alarm_read_as_before(self):
        with _at(12, 0):
            self.assertEqual(_panel({"armingMode": 0}).alarm_state, "armed_away")
            sounding = _panel(
                {"armingMode": 2, "jsonSchedule": _SCHEDULE},
                {STATION: {"alarmTriggered": True}},
            )
            self.assertEqual(sounding.alarm_state, "triggered")

    def test_the_panel_tells_a_schedule_slot_from_a_set_mode(self):
        with _at(12, 0):
            on_schedule = _panel({"armingMode": 2, "jsonSchedule": _SCHEDULE})
            self.assertEqual(on_schedule.alarm_state, "armed_home")
            self.assertEqual(
                on_schedule.extra_state_attributes,
                {"arming_mode": "schedule", "mode_id": 1, "source": "schedule"},
            )
            set_home = _panel({"armingMode": 1})
            self.assertEqual(set_home.alarm_state, "armed_home")
            self.assertEqual(
                set_home.extra_state_attributes,
                {"arming_mode": "home", "mode_id": 1, "source": "set"},
            )

    def test_on_schedule_the_panel_wakes_at_the_next_slot_boundary(self):
        entity = _panel({"armingMode": 2, "jsonSchedule": _SCHEDULE})
        with (
            patch.object(
                entity_module.dt_util,
                "now",
                return_value=datetime(2026, 9, 24, 12, 0),  # noqa: DTZ001
            ),
            patch.object(
                entity_module, "async_track_point_in_time", return_value=Mock()
            ) as track,
        ):
            entity._arm_boundary()
        self.assertEqual(track.call_args.args[2], datetime(2026, 9, 24, 20, 20))  # noqa: DTZ001


if __name__ == "__main__":
    unittest.main()
