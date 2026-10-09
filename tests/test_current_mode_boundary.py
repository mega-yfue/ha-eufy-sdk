# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, SLF001

import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from custom_components.eufy_sdk import entity as entity_module
from custom_components.eufy_sdk import sensor

STATION = "T8030P0000000001"
_SCHEDULE = {
    "schedules": [
        {"week": w, "start_h": 7, "start_m": 0, "end_h": 20, "end_m": 20, "mode_id": 1}
        for w in range(7)
    ]
}
_NOW = datetime(2026, 9, 24, 12, 0)  # noqa: DTZ001


class CurrentModeBoundaryTests(unittest.TestCase):
    def _sensor(self, state: dict) -> sensor.EufySdkCurrentModeSensor:
        coordinator = Mock()
        coordinator.data = {STATION: {"capabilities": ["arming"], "state": state}}
        entity = sensor.EufySdkCurrentModeSensor(coordinator, STATION)
        entity.hass = Mock()
        entity.async_write_ha_state = Mock()
        return entity

    def test_on_schedule_it_wakes_at_the_next_slot_boundary_and_rearms(self):
        entity = self._sensor({"armingMode": 2, "jsonSchedule": _SCHEDULE})
        unsub = Mock()
        with (
            patch.object(entity_module.dt_util, "now", return_value=_NOW),
            patch.object(
                entity_module, "async_track_point_in_time", return_value=unsub
            ) as track,
        ):
            entity._arm_boundary()
            self.assertEqual(track.call_args.args[2], datetime(2026, 9, 24, 20, 20))  # noqa: DTZ001

            entity._on_boundary(None)

        entity.async_write_ha_state.assert_called_once_with()
        self.assertEqual(track.call_count, 2)

    def test_rearming_cancels_the_pending_timer(self):
        entity = self._sensor({"armingMode": 2, "jsonSchedule": _SCHEDULE})
        first, second = Mock(), Mock()
        with (
            patch.object(entity_module.dt_util, "now", return_value=_NOW),
            patch.object(
                entity_module, "async_track_point_in_time", side_effect=[first, second]
            ),
        ):
            entity._arm_boundary()
            entity._arm_boundary()
        first.assert_called_once_with()
        second.assert_not_called()

    def test_off_schedule_no_timer_is_set(self):
        for state in ({"armingMode": 0}, {"armingMode": 47}, {}):
            entity = self._sensor({**state, "jsonSchedule": _SCHEDULE})
            with patch.object(entity_module, "async_track_point_in_time") as track:
                entity._arm_boundary()
            track.assert_not_called()


if __name__ == "__main__":
    unittest.main()
