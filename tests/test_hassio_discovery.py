# ruff: noqa: ANN001, ANN201, ANN202, D100, D101, D102, INP001, PT009, SLF001

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.const import CONF_HOST, CONF_PORT

from custom_components.eufy_sdk import config_flow
from custom_components.eufy_sdk.const import CONF_GO2RTC_RTSP_PORT

ADDON_UUID = "0123456789abcdef0123456789abcdef"


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
        flow._abort_if_unique_id_configured = Mock()
        flow.async_step_hassio_confirm = AsyncMock(return_value="confirm")
        return flow

    async def _discover(self, flow, info):
        with (
            patch.object(config_flow, "EufySdkApiClient") as client,
            patch.object(config_flow, "async_get_clientsession"),
        ):
            client.return_value.connect = AsyncMock()
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

    async def test_a_new_bridge_is_offered_for_confirmation(self):
        flow = self._flow(_entry("other", host="10.0.0.9"))
        result, client = await self._discover(flow, _discovery())
        self.assertEqual(result, "confirm")
        flow.async_set_unique_id.assert_awaited_once_with(ADDON_UUID)
        flow.async_update_reload_and_abort.assert_not_called()
        client.return_value.connect.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
