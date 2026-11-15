# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, SLF001

import unittest
from unittest.mock import Mock

from custom_components.eufy_sdk import event

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


if __name__ == "__main__":
    unittest.main()
