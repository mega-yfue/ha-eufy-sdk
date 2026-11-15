# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, SLF001

import unittest
from datetime import UTC, datetime
from unittest.mock import Mock

from custom_components.eufy_sdk import event, sensor

LOCK = "T85L000000000001"
CAMERA = "T8113N0000000001"


def _push(**data: object) -> Mock:
    """Build a bus event shaped like the bridge's decoded `lockState` push."""
    return Mock(data={"event": "lockState", "deviceSn": LOCK, **data})


class LockEventTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        coordinator = Mock()
        coordinator.data = {
            LOCK: {"capabilities": ["lock"]},
            CAMERA: {"capabilities": ["motion"]},
        }
        entry = Mock()
        entry.runtime_data.coordinator = coordinator
        added: list = []
        await event.async_setup_entry(Mock(), entry, added.extend)
        self.locks = [e for e in added if isinstance(e, event.EufySdkLockEvent)]
        self.lock = self.locks[0]
        self.lock.async_write_ha_state = Mock()

    def test_only_lock_capable_devices_get_a_lock_event(self):
        self.assertEqual(len(self.locks), 1)
        self.assertEqual(self.lock._sn, LOCK)
        self.assertEqual(self.lock.event_types, ["locked", "unlocked"])

    def test_a_fingerprint_unlock_fires_unlocked_with_method_and_user(self):
        self.lock._handle_event(_push(eventType=260, nick_name="Paul", locked=False))
        attrs = self.lock.state_attributes
        self.assertEqual(attrs["event_type"], "unlocked")
        self.assertEqual(attrs["method"], "fingerprint")
        self.assertEqual(attrs["user"], "Paul")
        self.assertEqual(attrs["code"], 260)
        self.lock.async_write_ha_state.assert_called_once_with()

    def test_an_auto_lock_fires_locked_automatic(self):
        self.lock._handle_event(_push(event_type=265, locked=True))
        attrs = self.lock.state_attributes
        self.assertEqual(attrs["event_type"], "locked")
        self.assertEqual(attrs["method"], "automatic")
        self.assertIsNone(attrs["user"])

    def test_an_unknown_code_falls_back_to_the_decoded_locked_bool(self):
        self.lock._handle_event(_push(eventType=999, locked=True))
        attrs = self.lock.state_attributes
        self.assertEqual(attrs["event_type"], "locked")
        self.assertIsNone(attrs["method"])

    def test_other_devices_and_events_are_ignored(self):
        self.lock._handle_event(_push(deviceSn=CAMERA, eventType=260, locked=False))
        self.lock._handle_event(
            Mock(data={"event": "motion", "deviceSn": LOCK, "eventType": 260})
        )
        self.lock._handle_event(_push(eventType=999))
        self.lock.async_write_ha_state.assert_not_called()


class LastLockSensorTests(unittest.TestCase):
    def setUp(self):
        coordinator = Mock()
        coordinator.data = {LOCK: {"capabilities": ["lock"]}}
        self.method = sensor.EufySdkLastLockMethodSensor(coordinator, LOCK)
        self.user = sensor.EufySdkLastLockUserSensor(coordinator, LOCK)
        for entity in (self.method, self.user):
            entity.async_write_ha_state = Mock()

    def _fire(self, push: Mock) -> None:
        for entity in (self.method, self.user):
            entity._handle_event(push)

    def test_the_method_sensor_is_an_enum_of_every_method(self):
        self.assertEqual(self.method.device_class, "enum")
        self.assertIn("fingerprint", self.method.options)
        self.assertIn("temporary_password", self.method.options)

    def test_an_unlock_records_method_and_user(self):
        self._fire(_push(eventType=260, nick_name="Paul", locked=False))
        self.assertEqual(self.method.native_value, "fingerprint")
        self.assertEqual(self.user.native_value, "Paul")
        self.assertEqual(self.method.extra_state_attributes["action"], "unlocked")

    def test_a_later_auto_lock_replaces_the_method(self):
        self._fire(_push(eventType=260, nick_name="Paul", locked=False))
        self._fire(_push(eventType=265, locked=True))
        self.assertEqual(self.method.native_value, "automatic")
        self.assertIsNone(self.user.native_value)
        self.assertEqual(self.user.extra_state_attributes["action"], "locked")

    def test_other_devices_and_events_are_ignored(self):
        self._fire(_push(deviceSn=CAMERA, eventType=260, locked=False))
        self._fire(Mock(data={"event": "motion", "deviceSn": LOCK}))
        self.method.async_write_ha_state.assert_not_called()
        self.assertIsNone(self.method.native_value)


class LastUnlockSensorTests(unittest.TestCase):
    def setUp(self):
        coordinator = Mock()
        coordinator.data = {LOCK: {"capabilities": ["lock"]}}
        self.unlock = sensor.EufySdkLastUnlockSensor(coordinator, LOCK)
        self.unlock.async_write_ha_state = Mock()

    def test_is_a_timestamp_sensor(self):
        self.assertEqual(self.unlock.device_class, "timestamp")

    def test_an_unlock_records_the_locks_own_event_time_in_utc(self):
        self.unlock._handle_event(
            _push(eventType=260, nick_name="Paul", event_time=1794785465000)
        )
        value = self.unlock.native_value
        self.assertEqual(value, datetime(2026, 11, 15, 23, 31, 5, tzinfo=UTC))
        self.assertEqual(self.unlock.extra_state_attributes["method"], "fingerprint")

    def test_a_missing_event_time_falls_back_to_now(self):
        before = datetime.now(UTC)
        self.unlock._handle_event(_push(eventType=261, nick_name="Paul"))
        self.assertGreaterEqual(self.unlock.native_value, before)

    def test_a_lock_report_leaves_the_last_unlock_alone(self):
        self.unlock._handle_event(_push(eventType=260, event_time=1794785465000))
        self.unlock._handle_event(_push(eventType=265, event_time=1794785495000))
        self.assertEqual(
            self.unlock.native_value, datetime(2026, 11, 15, 23, 31, 5, tzinfo=UTC)
        )
        self.unlock.async_write_ha_state.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
