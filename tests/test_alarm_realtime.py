# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009

import unittest
from unittest.mock import Mock

from custom_components.eufy_sdk import alarm_sync


def _coordinator() -> Mock:
    return Mock(
        data={
            "homebase": {"state": {"armingMode": 1}},
            "camera": {"state": {"motion": False}},
        }
    )


class AlarmRealtimeTests(unittest.TestCase):
    def test_trigger_marks_station_sounding(self):
        coordinator = _coordinator()

        phase = alarm_sync.apply_alarm_event(
            coordinator,
            {"event": "alarm", "deviceSn": "homebase", "type": 4, "phase": "triggered"},
        )

        self.assertEqual(phase, "triggered")
        state = coordinator.data["homebase"]["state"]
        self.assertTrue(state["alarmTriggered"])
        self.assertFalse(state["alarmPending"])
        self.assertEqual(state["alarmType"], 4)
        self.assertEqual(state["armingMode"], 1)
        coordinator.async_update_listeners.assert_called_once_with()
        self.assertEqual(coordinator.data["camera"], {"state": {"motion": False}})

    def test_stop_from_app_clears_and_records_who(self):
        coordinator = _coordinator()
        coordinator.data["homebase"]["state"]["alarmTriggered"] = True

        phase = alarm_sync.apply_alarm_event(
            coordinator,
            {
                "event": "alarm",
                "deviceSn": "homebase",
                "type": 16,
                "user_name": "danymexi",
                "phase": "triggered",
            },
        )

        self.assertEqual(phase, "stopped")
        state = coordinator.data["homebase"]["state"]
        self.assertFalse(state["alarmTriggered"])
        self.assertFalse(state["alarmPending"])
        self.assertEqual(state["alarmUser"], "danymexi")

    def test_delay_is_pending_until_the_trigger_lands(self):
        coordinator = _coordinator()

        self.assertEqual(
            alarm_sync.apply_alarm_event(
                coordinator,
                {"event": "alarm", "deviceSn": "homebase", "phase": "delayed"},
            ),
            "delayed",
        )
        state = coordinator.data["homebase"]["state"]
        self.assertTrue(state["alarmPending"])
        self.assertFalse(state["alarmTriggered"])

        alarm_sync.apply_alarm_event(
            coordinator,
            {"event": "alarm", "deviceSn": "homebase", "type": 3, "phase": "triggered"},
        )
        self.assertFalse(state["alarmPending"])
        self.assertTrue(state["alarmTriggered"])

    def test_unknown_device_and_other_events_are_ignored(self):
        coordinator = _coordinator()

        self.assertIsNone(
            alarm_sync.apply_alarm_event(
                coordinator,
                {"event": "alarm", "deviceSn": "missing", "type": 4},
            )
        )
        self.assertIsNone(
            alarm_sync.apply_alarm_event(
                coordinator,
                {"event": "armingModeChanged", "deviceSn": "homebase", "mode": 0},
            )
        )
        self.assertNotIn("alarmTriggered", coordinator.data["homebase"]["state"])
        coordinator.async_update_listeners.assert_not_called()

    def test_serial_falls_back_to_station_then_sn(self):
        self.assertEqual(
            alarm_sync.alarm_event_serial({"deviceSn": "a", "stationSn": "b"}), "a"
        )
        self.assertEqual(alarm_sync.alarm_event_serial({"stationSn": "b"}), "b")
        self.assertEqual(alarm_sync.alarm_event_serial({"sn": "c"}), "c")
        self.assertIsNone(alarm_sync.alarm_event_serial({}))

    def test_clear_alarm_only_notifies_when_something_was_set(self):
        coordinator = _coordinator()

        self.assertFalse(alarm_sync.clear_alarm(coordinator, "homebase"))
        self.assertFalse(alarm_sync.clear_alarm(coordinator, "missing"))
        coordinator.async_update_listeners.assert_not_called()

        coordinator.data["homebase"]["state"]["alarmTriggered"] = True
        self.assertTrue(alarm_sync.clear_alarm(coordinator, "homebase"))
        self.assertFalse(coordinator.data["homebase"]["state"]["alarmTriggered"])
        coordinator.async_update_listeners.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
