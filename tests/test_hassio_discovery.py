# ruff: noqa: ANN001, ANN201, ANN202, D100, D101, D102, INP001, PT009, SLF001

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.const import CONF_HOST, CONF_PORT

import custom_components.eufy_sdk as integration
from custom_components.eufy_sdk import config_flow
from custom_components.eufy_sdk.const import CONF_GO2RTC_RTSP_PORT

ADDON_UUID = "0123456789abcdef0123456789abcdef"
BRIDGE_ID = "3f2a0000-0000-4000-8000-000000000001"


def _discovery(host: str = "172.30.32.1", port: int = 3000, rtsp: int = 8554):
    return SimpleNamespace(
        name="eufy-sdk bridge",
        uuid=ADDON_UUID,
        config={CONF_HOST: host, CONF_PORT: port, "rtsp_port": rtsp},
    )


def _entry(unique_id: str, host: str = "172.30.32.1", port: int = 3000) -> Mock:
    entry = Mock()
    entry.unique_id = unique_id
    entry.data = {CONF_HOST: host, CONF_PORT: port, CONF_GO2RTC_RTSP_PORT: 8554}
    return entry


class HassioDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def _flow(self, *entries: Mock) -> config_flow.EufySdkFlowHandler:
        flow = config_flow.EufySdkFlowHandler()
        flow.hass = Mock()
        flow.context = {}
        flow._async_current_entries = Mock(return_value=list(entries))
        flow.async_update_reload_and_abort = Mock(return_value="aborted")
        flow.async_set_unique_id = AsyncMock()
        flow.async_abort = Mock(side_effect=lambda reason: f"abort:{reason}")
        flow._abort_if_unique_id_configured = Mock()
        flow.async_step_hassio_confirm = AsyncMock(return_value="confirm")
        return flow

    async def _discover(self, flow, info, bridge_id=None):
        with (
            patch.object(config_flow, "EufySdkApiClient") as client,
            patch.object(config_flow, "async_get_clientsession"),
        ):
            client.return_value.connect = AsyncMock()
            client.return_value.close = AsyncMock()
            client.return_value.auth_status = AsyncMock(return_value={"state": "ok"})
            client.return_value.bridge_id = bridge_id
            result = await flow.async_step_hassio(info)
        return result, client

    async def test_a_bridge_set_up_from_discovery_is_not_offered_again(self):
        entry = _entry(ADDON_UUID)
        flow = self._flow(entry)
        result, client = await self._discover(flow, _discovery())
        self.assertEqual(result, "aborted")
        flow.async_update_reload_and_abort.assert_called_once()
        args, kwargs = flow.async_update_reload_and_abort.call_args
        self.assertIs(args[0], entry)
        self.assertEqual(kwargs["reason"], "already_configured")
        self.assertFalse(kwargs["reload_even_if_entry_is_unchanged"])
        flow.async_step_hassio_confirm.assert_not_called()
        client.assert_not_called()

    async def test_a_manual_entry_for_the_same_bridge_is_not_offered_again(self):
        entry = _entry("172.30.32.1:3000")
        flow = self._flow(entry)
        result, _ = await self._discover(flow, _discovery())
        self.assertEqual(result, "aborted")
        flow.async_step_hassio_confirm.assert_not_called()

    async def test_a_moved_rtsp_port_is_written_to_the_existing_entry(self):
        flow = self._flow(_entry(ADDON_UUID))
        await self._discover(flow, _discovery(rtsp=8599))
        updates = flow.async_update_reload_and_abort.call_args.kwargs["data_updates"]
        self.assertEqual(updates[CONF_GO2RTC_RTSP_PORT], 8599)

    async def test_a_reachable_pinned_address_is_kept_on_rediscovery(self):
        # User pinned the add-on hostname (host ports unpublished); the add-on
        # re-announces its unreachable gateway address. Keep the working one. #142.
        entry = _entry(ADDON_UUID, host="local-eufy-sdk")
        flow = self._flow(entry)
        result, client = await self._discover(flow, _discovery(host="172.30.32.1"))
        self.assertEqual(result, "aborted")
        kwargs = flow.async_update_reload_and_abort.call_args.kwargs
        self.assertIsNone(kwargs["data_updates"])  # stored address left intact
        self.assertEqual(kwargs["reason"], "already_configured")
        client.return_value.connect.assert_awaited_once()  # probed the stored address
        client.return_value.close.assert_awaited_once()

    async def test_a_stale_pinned_address_is_replaced_on_rediscovery(self):
        # When the stored address no longer reaches the bridge, adopt the announced one.
        entry = _entry(ADDON_UUID, host="local-eufy-sdk")
        flow = self._flow(entry)
        with (
            patch.object(config_flow, "EufySdkApiClient") as client,
            patch.object(config_flow, "async_get_clientsession"),
        ):
            client.return_value.connect = AsyncMock(
                side_effect=config_flow.EufySdkApiClientError("bridge down")
            )
            client.return_value.close = AsyncMock()
            result = await flow.async_step_hassio(_discovery(host="172.30.32.1"))
        self.assertEqual(result, "aborted")
        updates = flow.async_update_reload_and_abort.call_args.kwargs["data_updates"]
        self.assertEqual(
            updates[CONF_HOST], "172.30.32.1"
        )  # adopted the discovered address
        client.return_value.close.assert_awaited_once()

    async def test_a_new_bridge_is_offered_for_confirmation(self):
        flow = self._flow(_entry("other", host="10.0.0.9"))
        result, client = await self._discover(flow, _discovery())
        self.assertEqual(result, "confirm")
        flow.async_set_unique_id.assert_awaited_once_with(ADDON_UUID)
        flow.async_update_reload_and_abort.assert_not_called()
        client.return_value.connect.assert_awaited_once()

    async def test_a_bridge_added_under_another_address_is_not_offered_again(self):
        # Two users on 0.5.0: added by LAN IP, then discovered at 172.30.32.1.
        flow = self._flow(_entry(BRIDGE_ID, host="192.168.1.20"))
        result, client = await self._discover(flow, _discovery(), bridge_id=BRIDGE_ID)
        self.assertEqual(result, "abort:already_configured")
        flow.async_step_hassio_confirm.assert_not_called()
        client.return_value.close.assert_awaited_once()

    async def test_a_new_bridge_is_keyed_by_its_bridge_id(self):
        flow = self._flow(_entry("other", host="10.0.0.9"))
        result, _ = await self._discover(flow, _discovery(), bridge_id=BRIDGE_ID)
        self.assertEqual(result, "confirm")
        flow.async_set_unique_id.assert_awaited_with(BRIDGE_ID, raise_on_progress=False)

    async def test_an_older_bridge_without_an_id_is_offered_as_before(self):
        flow = self._flow(_entry("other", host="10.0.0.9"))
        result, _ = await self._discover(flow, _discovery(), bridge_id=None)
        self.assertEqual(result, "confirm")


class AdoptBridgeIdTests(unittest.TestCase):
    def _hass(self, *entries: SimpleNamespace) -> Mock:
        hass = Mock()
        hass.config_entries.async_entries = Mock(return_value=list(entries))
        return hass

    def _cfg(self, entry_id, unique_id, title="eufy bridge"):
        return SimpleNamespace(
            entry_id=entry_id, unique_id=unique_id, title=title, domain="eufy_sdk"
        )

    def test_an_entry_adopts_its_bridges_id(self):
        entry = self._cfg("a", "192.168.1.20:3000")
        hass = self._hass(entry)
        integration._adopt_bridge_id(hass, entry, BRIDGE_ID)
        hass.config_entries.async_update_entry.assert_called_once_with(
            entry, unique_id=BRIDGE_ID
        )

    def test_a_duplicate_entry_is_reported_not_rekeyed(self):
        first = self._cfg("a", BRIDGE_ID, "first")
        second = self._cfg("b", ADDON_UUID, "second")
        hass = self._hass(first, second)
        with self.assertLogs(integration.LOGGER, level="WARNING"):
            integration._adopt_bridge_id(hass, second, BRIDGE_ID)
        hass.config_entries.async_update_entry.assert_not_called()

    def test_an_older_bridge_changes_nothing(self):
        entry = self._cfg("a", "192.168.1.20:3000")
        hass = self._hass(entry)
        integration._adopt_bridge_id(hass, entry, None)
        hass.config_entries.async_update_entry.assert_not_called()


if __name__ == "__main__":
    unittest.main()
