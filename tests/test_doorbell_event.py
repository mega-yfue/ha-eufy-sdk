# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, SLF001

import unittest
from unittest.mock import Mock

from homeassistant.components.event import DoorbellEventType, EventDeviceClass

from custom_components.eufy_sdk import event

DOORBELL = "T8214P0000000001"


class DoorbellEventTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        coordinator = Mock()
        coordinator.data = {DOORBELL: {"capabilities": ["doorbell"]}}
        entry = Mock()
        entry.runtime_data.coordinator = coordinator
        added: list = []
        await event.async_setup_entry(Mock(), entry, added.extend)
        self.doorbell = next(
            e for e in added if isinstance(e, event.EufySdkDoorbellEvent)
        )

    def test_a_doorbell_event_offers_home_assistants_ring_type(self):
        # Home Assistant rejects a doorbell event entity without `ring` from 2027.4.
        self.assertEqual(self.doorbell.device_class, EventDeviceClass.DOORBELL)
        self.assertEqual(self.doorbell.event_types, [DoorbellEventType.RING])

    def test_a_doorbell_press_fires_ring(self):
        self.assertEqual(self.doorbell._match("doorbellPress"), DoorbellEventType.RING)

    def test_other_bridge_events_do_not_fire_the_doorbell(self):
        self.assertIsNone(self.doorbell._match("motion"))
        self.assertIsNone(self.doorbell._match(None))


if __name__ == "__main__":
    unittest.main()
