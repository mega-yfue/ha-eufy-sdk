# ruff: noqa: ANN201, D100, D102, INP001, PT009, SLF001

import unittest
from unittest.mock import AsyncMock, Mock

from custom_components.eufy_sdk.button import EufySdkOpenLidButton, async_setup_entry

STANDALONE = "T8790N0000000001"
ON_HUB = "T8790N0000000002"
CAMERA = "T8161P0000000001"


def _coordinator() -> Mock:
    coord = Mock()
    coord.data = {
        # A standalone SmartDrop: the bridge reports the smart_drop capability.
        STANDALONE: {
            "name": "Parcel box",
            "capabilities": ["camera", "battery", "smart_drop"],
        },
        # Paired to a HomeBase S1 Pro: no smart_drop, the hub refuses the open.
        ON_HUB: {"name": "Parcel box 2", "capabilities": ["camera", "battery"]},
        CAMERA: {"name": "Garden", "capabilities": ["camera", "battery"]},
    }
    coord.config_entry.runtime_data.client.action = AsyncMock(return_value=None)
    return coord


class OpenLidButtonTest(unittest.IsolatedAsyncioTestCase):
    """The lid button exists only where the bridge reports smart_drop, and opens it."""

    async def test_only_a_device_with_smart_drop_gets_the_button(self):
        coord = _coordinator()
        entry = Mock()
        entry.runtime_data.coordinator = coord
        added: list = []
        await async_setup_entry(Mock(), entry, added.extend)

        lids = [e for e in added if isinstance(e, EufySdkOpenLidButton)]
        self.assertEqual([e._sn for e in lids], [STANDALONE])
        self.assertEqual(lids[0].unique_id, f"{STANDALONE}_open_lid")
        self.assertEqual(lids[0].translation_key, "open_lid")

    async def test_press_sends_open_lid_to_the_bridge(self):
        coord = _coordinator()
        button = EufySdkOpenLidButton(coord, STANDALONE)

        await button.async_press()

        client = coord.config_entry.runtime_data.client
        client.action.assert_awaited_once_with(STANDALONE, "openLid")


if __name__ == "__main__":
    unittest.main()
