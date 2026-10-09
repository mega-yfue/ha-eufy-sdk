# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009

import unittest
from unittest.mock import Mock

from custom_components.eufy_sdk import alarm_sync
from custom_components.eufy_sdk.alarm_logic import AlarmState, panel_state_for


def _coordinator() -> Mock:
    return Mock(
        data={
            "homebase": {"state": {"armingMode": 1}},
            "camera": {"state": {"motion": False}},
        }
    )


def _trigger() -> dict:
    return {"event": "alarm", "deviceSn": "homebase", "type": 4, "phase": "triggered"}


class AlarmRealtimeTests(unittest.TestCase):
    def test_trigger_marks_station_sounding(self):
        coordinator, alarms = _coordinator(), {}

        phase = alarm_sync.apply_alarm_event(coordinator, alarms, _trigger())

        self.assertEqual(phase, "triggered")
        self.assertTrue(alarms["homebase"]["alarmTriggered"])
        self.assertFalse(alarms["homebase"]["alarmPending"])
        self.assertEqual(alarms["homebase"]["alarmType"], 4)
        # The polled device state is left alone: the lifecycle lives in its own map.
        self.assertEqual(coordinator.data["homebase"], {"state": {"armingMode": 1}})
        coordinator.async_update_listeners.assert_called_once_with()

    def test_a_poll_replacing_the_device_state_keeps_the_alarm(self):
        coordinator, alarms = _coordinator(), {}
        alarm_sync.apply_alarm_event(coordinator, alarms, _trigger())

        # A refresh rebuilds coordinator.data from the bridge with fresh dicts.
        coordinator.data = {"homebase": {"state": {"armingMode": 0}}}

        self.assertTrue(alarms["homebase"]["alarmTriggered"])
        self.assertEqual(
            panel_state_for(coordinator.data["homebase"]["state"], alarms["homebase"]),
            AlarmState.TRIGGERED,
        )

    def test_stop_from_app_clears_and_records_who(self):
        coordinator, alarms = _coordinator(), {}
        alarm_sync.apply_alarm_event(coordinator, alarms, _trigger())

        phase = alarm_sync.apply_alarm_event(
            coordinator,
            alarms,
            {
                "event": "alarm",
                "deviceSn": "homebase",
                "type": 16,
                "user_name": "danymexi",
                "phase": "triggered",
            },
        )

        self.assertEqual(phase, "stopped")
        self.assertFalse(alarms["homebase"]["alarmTriggered"])
        self.assertFalse(alarms["homebase"]["alarmPending"])
        self.assertEqual(alarms["homebase"]["alarmUser"], "danymexi")

    def test_delay_is_pending_until_the_trigger_lands(self):
        coordinator, alarms = _coordinator(), {}

        self.assertEqual(
            alarm_sync.apply_alarm_event(
                coordinator,
                alarms,
                {"event": "alarm", "deviceSn": "homebase", "phase": "delayed"},
            ),
            "delayed",
        )
        self.assertTrue(alarms["homebase"]["alarmPending"])
        self.assertFalse(alarms["homebase"]["alarmTriggered"])

        alarm_sync.apply_alarm_event(
            coordinator,
            alarms,
            {"event": "alarm", "deviceSn": "homebase", "type": 3, "phase": "triggered"},
        )
        self.assertFalse(alarms["homebase"]["alarmPending"])
        self.assertTrue(alarms["homebase"]["alarmTriggered"])

    def test_clear_pending_ends_only_a_countdown(self):
        coordinator, alarms = _coordinator(), {}
        self.assertFalse(alarm_sync.clear_pending(coordinator, alarms, "homebase"))

        alarm_sync.apply_alarm_event(
            coordinator,
            alarms,
            {"event": "alarm", "deviceSn": "homebase", "phase": "delayed"},
        )
        self.assertTrue(alarm_sync.clear_pending(coordinator, alarms, "homebase"))
        self.assertFalse(alarms["homebase"]["alarmPending"])

        # A sounding alarm is not a countdown: a mode change must not silence it.
        alarm_sync.apply_alarm_event(coordinator, alarms, _trigger())
        self.assertFalse(alarm_sync.clear_pending(coordinator, alarms, "homebase"))
        self.assertTrue(alarms["homebase"]["alarmTriggered"])

    def test_disarm_or_home_during_a_countdown_ends_pending(self):
        delayed = {"event": "alarm", "deviceSn": "homebase", "phase": "delayed"}
        for mode, ends in ((63, True), (1, True), (0, False), (3, False)):
            coordinator, alarms = _coordinator(), {}
            alarm_sync.apply_alarm_event(coordinator, alarms, delayed)
            coordinator.data["homebase"]["state"]["armingMode"] = mode

            self.assertEqual(
                alarm_sync.end_cancelled_delay(coordinator, alarms, "homebase"), ends
            )
            self.assertEqual(alarms["homebase"]["alarmPending"], not ends)
        self.assertFalse(alarm_sync.end_cancelled_delay(_coordinator(), {}, "missing"))

    def test_unknown_device_and_other_events_are_ignored(self):
        coordinator, alarms = _coordinator(), {}

        self.assertIsNone(
            alarm_sync.apply_alarm_event(
                coordinator,
                alarms,
                {"event": "alarm", "deviceSn": "missing", "type": 4},
            )
        )
        self.assertIsNone(
            alarm_sync.apply_alarm_event(
                coordinator,
                alarms,
                {"event": "armingModeChanged", "deviceSn": "homebase", "mode": 0},
            )
        )
        self.assertEqual(alarms, {})
        coordinator.async_update_listeners.assert_not_called()

    def test_a_camera_tripped_alarm_lands_on_its_station(self):
        # Shape of a live push from a T8113 attached to a T8030 in Away: the camera is
        # the deviceSn, the hub only appears in rec_content[].station_sn.
        push = {
            "event": "alarm",
            "deviceSn": "camera",
            "s": "camera",
            "type": 7,
            "phase": "triggered",
            "rec_content": [{"station_sn": "homebase", "device_sn": "other"}],
        }
        for camera_record in ({"stationSn": "homebase"}, {}):
            coordinator = Mock(
                data={
                    "homebase": {"state": {"armingMode": 0}},
                    "camera": {**camera_record, "state": {}},
                }
            )
            alarms: dict = {}
            self.assertEqual(
                alarm_sync.apply_alarm_event(coordinator, alarms, push), "triggered"
            )
            self.assertEqual(set(alarms), {"homebase"})
            self.assertTrue(alarms["homebase"]["alarmTriggered"])

    def test_station_serial_keeps_a_station_and_unknown_stations(self):
        coordinator = _coordinator()
        self.assertEqual(
            alarm_sync.alarm_station_serial(coordinator, {"deviceSn": "homebase"}),
            "homebase",
        )
        # A station the device list doesn't know is not guessed at.
        push = {"deviceSn": "camera", "rec_content": [{"station_sn": "missing"}]}
        self.assertEqual(alarm_sync.alarm_station_serial(coordinator, push), "camera")

    def test_serial_falls_back_to_station_then_sn(self):
        self.assertEqual(
            alarm_sync.alarm_event_serial({"deviceSn": "a", "stationSn": "b"}), "a"
        )
        self.assertEqual(alarm_sync.alarm_event_serial({"stationSn": "b"}), "b")
        self.assertEqual(alarm_sync.alarm_event_serial({"sn": "c"}), "c")
        self.assertIsNone(alarm_sync.alarm_event_serial({}))

    def test_clear_alarm_only_notifies_when_something_was_set(self):
        coordinator, alarms = _coordinator(), {}

        self.assertFalse(alarm_sync.clear_alarm(coordinator, alarms, "homebase"))
        self.assertFalse(alarm_sync.clear_alarm(coordinator, alarms, "missing"))
        coordinator.async_update_listeners.assert_not_called()

        alarm_sync.apply_alarm_event(coordinator, alarms, _trigger())
        coordinator.async_update_listeners.reset_mock()
        self.assertTrue(alarm_sync.clear_alarm(coordinator, alarms, "homebase"))
        self.assertFalse(alarms["homebase"]["alarmTriggered"])
        coordinator.async_update_listeners.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
