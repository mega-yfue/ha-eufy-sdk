# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, SLF001

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from custom_components.eufy_sdk import number

STATION = "T8030P0000000001"

# The spec shape the bridge sends for a HomeBase's write-only settings (read off a
# live T8030's `device.properties`), next to an ordinary reported number.
ALARM_VOLUME = {
    "name": "alarmVolume",
    "type": "number",
    "unit": "%",
    "kind": "percent",
    "writable": True,
    "writeOnly": True,
    "min": 0,
    "max": 100,
    "description": "HomeBase alarm volume 0..100 (1235 CMD_SET_HUB_SPK_VOLUME).",
}
SPEAKER_VOLUME = {
    "name": "speakerVolume",
    "type": "number",
    "unit": "%",
    "kind": "percent",
    "writable": True,
}


class WriteOnlyNumberTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.coordinator = Mock()
        self.coordinator.data = {
            STATION: {"capabilities": ["siren"], "state": {"speakerVolume": 40}}
        }
        self.coordinator.async_request_refresh = AsyncMock()
        entry = Mock()
        entry.runtime_data.coordinator = self.coordinator
        entry.runtime_data.properties = {STATION: [ALARM_VOLUME, SPEAKER_VOLUME]}
        entry.runtime_data.client.set_property = AsyncMock()
        self.coordinator.config_entry = entry
        self.client = entry.runtime_data.client
        added: list = []
        with patch.object(number, "solix_devices_with", return_value=[]):
            await number.async_setup_entry(Mock(), entry, added.extend)
        self.numbers = {e._prop: e for e in added}
        for e in added:
            e.hass = Mock()
            e.async_write_ha_state = Mock()

    def test_write_only_setting_is_an_assumed_number_with_its_own_range(self):
        alarm = self.numbers["alarmVolume"]
        self.assertTrue(alarm.assumed_state)
        self.assertEqual((alarm.native_min_value, alarm.native_max_value), (0, 100))
        self.assertIsNone(alarm.native_value)
        self.assertFalse(self.numbers["speakerVolume"].assumed_state)
        self.assertEqual(self.numbers["speakerVolume"].native_value, 40.0)

    async def test_written_value_is_held_not_reconciled_away(self):
        alarm = self.numbers["alarmVolume"]
        with patch("custom_components.eufy_sdk.entity.async_call_later") as later:
            await alarm.async_set_native_value(35.0)
        self.client.set_property.assert_awaited_once_with(STATION, "alarmVolume", 35)
        later.assert_not_called()
        self.assertEqual(alarm.native_value, 35.0)
        # A later poll still carries no alarmVolume; the written value stands.
        self.coordinator.data[STATION]["state"] = {"speakerVolume": 40}
        self.assertEqual(alarm.native_value, 35.0)

    async def test_reported_number_still_reconciles_after_a_write(self):
        speaker = self.numbers["speakerVolume"]
        with patch("custom_components.eufy_sdk.entity.async_call_later") as later:
            await speaker.async_set_native_value(60.0)
        later.assert_called_once()

    async def test_write_only_value_is_restored_across_restart(self):
        alarm = self.numbers["alarmVolume"]
        alarm.async_get_last_number_data = AsyncMock(
            return_value=SimpleNamespace(native_value=55.0)
        )
        with patch.object(
            number.EufySdkPropertyEntity, "async_added_to_hass", AsyncMock()
        ):
            await alarm.async_added_to_hass()
        self.assertEqual(alarm.native_value, 55.0)

    async def test_reported_number_is_not_restored(self):
        speaker = self.numbers["speakerVolume"]
        speaker.async_get_last_number_data = AsyncMock(
            return_value=SimpleNamespace(native_value=99.0)
        )
        with patch.object(
            number.EufySdkPropertyEntity, "async_added_to_hass", AsyncMock()
        ):
            await speaker.async_added_to_hass()
        speaker.async_get_last_number_data.assert_not_awaited()
        self.assertEqual(speaker.native_value, 40.0)
