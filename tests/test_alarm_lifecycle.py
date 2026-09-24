# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, SLF001

import unittest
from unittest.mock import Mock, patch

import custom_components.eufy_sdk as integration


def _trigger(serial: str = "homebase") -> dict:
    return {"event": "alarm", "deviceSn": serial, "type": 4, "phase": "triggered"}


def _stop(serial: str = "homebase") -> dict:
    return {"event": "alarm", "deviceSn": serial, "type": 16, "phase": "triggered"}


class AlarmLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.hass = Mock()
        self.entry = Mock()
        self.entry.async_on_unload = Mock()
        self.coordinator = Mock(data={"homebase": {"state": {"armingMode": 0}}})
        self.scheduled: list[tuple[float, object]] = []
        self.cancels: list[Mock] = []

        def fake_call_later(_hass: object, delay: float, action: object) -> Mock:
            cancel = Mock()
            self.scheduled.append((delay, action))
            self.cancels.append(cancel)
            return cancel

        patcher = patch.object(
            integration, "async_call_later", side_effect=fake_call_later
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.on_alarm = integration._alarm_lifecycle(
            self.hass, self.entry, self.coordinator
        )

    def _state(self) -> dict:
        return self.coordinator.data["homebase"]["state"]

    def test_start_arms_the_fallback_and_a_stop_push_cancels_it(self):
        self.on_alarm(_trigger())

        self.assertTrue(self._state()["alarmTriggered"])
        self.assertEqual(len(self.scheduled), 1)
        self.assertEqual(self.scheduled[0][0], integration.ALARM_AUTO_CLEAR_SECONDS)

        self.on_alarm(_stop())

        self.cancels[0].assert_called_once_with()
        self.assertFalse(self._state()["alarmTriggered"])
        self.assertEqual(len(self.scheduled), 1)

    def test_fallback_clears_the_flags_when_no_stop_ever_arrives(self):
        self.on_alarm(_trigger())
        _delay, fire = self.scheduled[0]

        fire(None)

        self.assertFalse(self._state()["alarmTriggered"])
        self.assertFalse(self._state()["alarmPending"])
        self.assertEqual(self._state()["armingMode"], 0)

    def test_a_new_start_rearms_instead_of_stacking_timers(self):
        self.on_alarm(_trigger())
        self.on_alarm(_trigger())

        self.cancels[0].assert_called_once_with()
        self.cancels[1].assert_not_called()
        self.assertEqual(len(self.scheduled), 2)

    def test_unknown_station_schedules_nothing(self):
        self.on_alarm(_trigger("missing"))

        self.assertEqual(self.scheduled, [])
        self.coordinator.async_update_listeners.assert_not_called()

    def test_unload_cancels_whatever_is_still_pending(self):
        self.on_alarm(_trigger())
        self.entry.async_on_unload.assert_called_once()
        (cancel_all,) = self.entry.async_on_unload.call_args.args

        cancel_all()

        self.cancels[0].assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
