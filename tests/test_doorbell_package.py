# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, SLF001

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from custom_components.eufy_sdk import binary_sensor
from custom_components.eufy_sdk.const import EVENT_TYPE

DOORBELL = "T8214P0000000001"
CAMERA = "T8425P0000000001"


def push(event: str, sn: str = DOORBELL) -> SimpleNamespace:
    """Build a bridge push as the integration re-fires it on the bus."""
    return SimpleNamespace(event_type=EVENT_TYPE, data={"event": event, "deviceSn": sn})


class DoorbellPackageTests(unittest.IsolatedAsyncioTestCase):
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
        self.sensors = {
            e._sn: e
            for e in added
            if isinstance(e, binary_sensor.EufyPackageBinarySensor)
        }
        self.sensor = self.sensors[DOORBELL]
        self.sensor.async_write_ha_state = Mock()

    def test_only_a_doorbell_gets_a_package_sensor(self):
        self.assertEqual(set(self.sensors), {DOORBELL})
        self.assertEqual(self.sensor.unique_id, f"{DOORBELL}_package")
        self.assertFalse(self.sensor.is_on)

    def test_latches_on_delivery_and_clears_on_pickup(self):
        self.sensor._handle_event(push("packageDelivered"))
        self.assertTrue(self.sensor.is_on)
        self.sensor._handle_event(push("packageStranded"))
        self.assertTrue(self.sensor.is_on)
        self.sensor._handle_event(push("packageTaken"))
        self.assertFalse(self.sensor.is_on)
        self.assertEqual(self.sensor.async_write_ha_state.call_count, 3)

    def test_ignores_other_devices_and_events(self):
        self.sensor._handle_event(push("packageDelivered", sn=CAMERA))
        self.sensor._handle_event(push("doorbellPress"))
        self.assertFalse(self.sensor.is_on)
        self.sensor.async_write_ha_state.assert_not_called()

    async def test_restores_a_waiting_package_across_restart(self):
        self.sensor.hass = Mock()
        self.sensor.async_get_last_state = AsyncMock(
            return_value=SimpleNamespace(state="on")
        )
        await self.sensor.async_added_to_hass()
        self.assertTrue(self.sensor.is_on)
