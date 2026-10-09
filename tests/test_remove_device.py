# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import custom_components.eufy_sdk as integration
from custom_components.eufy_sdk.const import DOMAIN


def _entry(eufy: dict, solix: dict | None = None, *, polled: bool = True) -> Mock:
    entry = Mock()
    entry.runtime_data.coordinator.last_update_success = polled
    entry.runtime_data.coordinator.data = eufy
    entry.runtime_data.coordinator.solix_devices = solix or {}
    return entry


def _device(*identifiers: tuple[str, str]) -> SimpleNamespace:
    return SimpleNamespace(identifiers=set(identifiers))


class RemoveDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def _allowed(self, entry: Mock, device: SimpleNamespace) -> bool:
        return await integration.async_remove_config_entry_device(Mock(), entry, device)

    async def test_a_device_the_bridge_no_longer_lists_can_be_removed(self):
        entry = _entry({"T8030LIVE": {"sn": "T8030LIVE"}})
        self.assertTrue(await self._allowed(entry, _device((DOMAIN, "T8030GONE"))))

    async def test_a_device_the_bridge_still_lists_is_kept(self):
        entry = _entry({"T8030LIVE": {"sn": "T8030LIVE"}})
        self.assertFalse(await self._allowed(entry, _device((DOMAIN, "T8030LIVE"))))

    async def test_solix_devices_are_checked_against_the_solix_list(self):
        entry = _entry({}, solix={"SB1": {"sn": "SB1"}})
        self.assertFalse(await self._allowed(entry, _device((DOMAIN, "solix:SB1"))))
        self.assertTrue(await self._allowed(entry, _device((DOMAIN, "solix:SB2"))))

    async def test_a_solix_serial_is_not_confused_with_a_eufy_one(self):
        entry = _entry({"SB1": {"sn": "SB1"}})
        self.assertTrue(await self._allowed(entry, _device((DOMAIN, "solix:SB1"))))

    async def test_nothing_is_removed_while_the_last_poll_failed(self):
        entry = _entry({}, polled=False)
        self.assertFalse(await self._allowed(entry, _device((DOMAIN, "T8030GONE"))))
        self.assertFalse(await self._allowed(entry, _device((DOMAIN, "solix:SB2"))))

    async def test_identifiers_of_other_domains_are_ignored(self):
        entry = _entry({"T8030LIVE": {"sn": "T8030LIVE"}})
        device = _device(("other", "T8030LIVE"), (DOMAIN, "T8030GONE"))
        self.assertTrue(await self._allowed(entry, device))


if __name__ == "__main__":
    unittest.main()
