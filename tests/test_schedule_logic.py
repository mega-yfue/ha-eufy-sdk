# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009

import unittest
from datetime import datetime

from custom_components.eufy_sdk import schedule_logic

# A real HomeBase 3 timetable (param 1254), one week identical every day:
# 00:00-07:00 away, 07:00-20:20 home, 20:20-23:59 custom1.
_SCHEDULE = {
    "account_id": "x",
    "schedules": [
        slot
        for week in range(7)
        for slot in (
            {
                "week": week,
                "start_h": 0,
                "start_m": 0,
                "end_h": 7,
                "end_m": 0,
                "mode_id": 0,
            },
            {
                "week": week,
                "start_h": 7,
                "start_m": 0,
                "end_h": 20,
                "end_m": 20,
                "mode_id": 1,
            },
            {
                "week": week,
                "start_h": 20,
                "start_m": 20,
                "end_h": 23,
                "end_m": 59,
                "mode_id": 3,
            },
        )
    ],
}

# Thursday 24 Sep 2026 — the day the 20:20 switch to custom1 was watched live.
_THU_2019 = datetime(2026, 9, 24, 20, 19)  # noqa: DTZ001
_THU_2020 = datetime(2026, 9, 24, 20, 20)  # noqa: DTZ001
_THU_0300 = datetime(2026, 9, 24, 3, 0)  # noqa: DTZ001


class ScheduleLogicTests(unittest.TestCase):
    def test_resolves_the_slot_in_force_and_switches_on_the_boundary_minute(self):
        self.assertEqual(schedule_logic.resolve_schedule(_SCHEDULE, _THU_0300), 0)
        self.assertEqual(schedule_logic.resolve_schedule(_SCHEDULE, _THU_2019), 1)
        self.assertEqual(schedule_logic.resolve_schedule(_SCHEDULE, _THU_2020), 3)
        last_minute = datetime(2026, 9, 24, 23, 59)  # noqa: DTZ001
        self.assertEqual(schedule_logic.resolve_schedule(_SCHEDULE, last_minute), 3)

    def test_weekday_follows_the_hub_sunday_zero(self):
        sunday_only = {
            "schedules": [
                {
                    "week": 0,
                    "start_h": 0,
                    "start_m": 0,
                    "end_h": 23,
                    "end_m": 59,
                    "mode_id": 5,
                }
            ]
        }
        sunday = datetime(2026, 9, 27, 12, 0)  # noqa: DTZ001
        monday = datetime(2026, 9, 28, 12, 0)  # noqa: DTZ001
        self.assertEqual(schedule_logic.resolve_schedule(sunday_only, sunday), 5)
        self.assertIsNone(schedule_logic.resolve_schedule(sunday_only, monday))

    def test_malformed_timetables_resolve_to_nothing(self):
        for bad in (None, "x", {}, {"schedules": "x"}, {"schedules": [{"week": 4}]}):
            self.assertIsNone(schedule_logic.resolve_schedule(bad, _THU_2020))

    def test_pushed_current_mode_wins_over_everything(self):
        state = {"armingMode": 2, "currentMode": 1, "jsonSchedule": _SCHEDULE}
        self.assertEqual(schedule_logic.current_mode_for(state, _THU_2020), (1, "push"))

    def test_schedule_resolves_locally_before_the_first_push(self):
        state = {"armingMode": 2, "jsonSchedule": _SCHEDULE}
        self.assertEqual(
            schedule_logic.current_mode_for(state, _THU_2020), (3, "schedule")
        )
        self.assertEqual(
            schedule_logic.current_mode_for({"armingMode": 2}, _THU_2020),
            (None, "schedule"),
        )

    def test_a_plain_set_mode_is_the_current_mode(self):
        self.assertEqual(
            schedule_logic.current_mode_for({"armingMode": 0}, _THU_2020), (0, "set")
        )
        self.assertEqual(schedule_logic.current_mode_for({}, _THU_2020), (None, "set"))

    def test_labels_match_the_sdk_enum(self):
        self.assertEqual(schedule_logic.mode_label(3), "custom1")
        self.assertEqual(schedule_logic.mode_label("63"), "disarmed")
        not_a_mode = True
        self.assertIsNone(schedule_logic.mode_label(not_a_mode))
        self.assertIsNone(schedule_logic.mode_label(99))


if __name__ == "__main__":
    unittest.main()
