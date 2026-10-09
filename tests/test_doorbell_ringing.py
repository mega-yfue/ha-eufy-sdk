# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, SLF001

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from custom_components.eufy_sdk import binary_sensor
from custom_components.eufy_sdk.const import EVENT_TYPE

DOORBELL = "T8214P0000000001"
CAMERA = "T8425P0000000001"


def ringing_sensors(entities: list) -> dict[str, binary_sensor.EufyPushBinarySensor]:
    """Map device serial -> its Ringing sensor, from a platform setup's entities."""
    return {
        e._sn: e
        for e in entities
        if isinstance(e, binary_sensor.EufyPushBinarySensor)
        and e.unique_id.endswith("_ringing")
    }


class DoorbellRingingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        coordinator = Mock()
        coordinator.data = {
            DOORBELL: {"capabilities": ["doorbell", "motion"]},
            CAMERA: {"capabilities": ["motion"]},
        }
        coordinator.solix_devices = {}
        entry = Mock()
        entry.runtime_data.coordinator = coordinator
        entry.runtime_data.properties = {}
        added: list = []
        await binary_sensor.async_setup_entry(Mock(), entry, added.extend)
        self.sensors = ringing_sensors(added)

    def test_only_a_doorbell_gets_a_ringing_sensor(self):
        self.assertEqual(set(self.sensors), {DOORBELL})
        self.assertEqual(self.sensors[DOORBELL].unique_id, f"{DOORBELL}_ringing")

    def test_a_press_rings_then_auto_clears(self):
        sensor = self.sensors[DOORBELL]
        sensor.hass = Mock()
        sensor.async_write_ha_state = Mock()
        fire = SimpleNamespace(
            event_type=EVENT_TYPE, data={"event": "doorbellPress", "deviceSn": DOORBELL}
        )
        with patch.object(binary_sensor, "async_call_later") as later:
            sensor._handle_event(fire)
            self.assertTrue(sensor.is_on)
            later.assert_called_once()
            later.call_args.args[2](None)  # the auto-off callback fires
        self.assertFalse(sensor.is_on)

    def test_ignores_other_devices_and_events(self):
        sensor = self.sensors[DOORBELL]
        sensor.hass = Mock()
        sensor.async_write_ha_state = Mock()
        with patch.object(binary_sensor, "async_call_later"):
            for data in (
                {"event": "doorbellPress", "deviceSn": CAMERA},
                {"event": "motion", "deviceSn": DOORBELL},
            ):
                sensor._handle_event(SimpleNamespace(event_type=EVENT_TYPE, data=data))
        self.assertFalse(sensor.is_on)
