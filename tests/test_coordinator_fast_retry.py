# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, SLF001

import unittest
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.eufy_sdk import coordinator as coord_mod
from custom_components.eufy_sdk.api import (
    EufySdkApiClient,
    EufySdkApiClientCommunicationError,
    EufySdkApiClientError,
)


def _coordinator(client: Mock) -> coord_mod.EufySdkDataUpdateCoordinator:
    # Skip DataUpdateCoordinator.__init__: only the poll body and the retry
    # bookkeeping are under test.
    c = object.__new__(coord_mod.EufySdkDataUpdateCoordinator)
    c.hass = Mock()
    c.config_entry = Mock()
    c.config_entry.runtime_data.client = client
    c._fast_retry_cancel = None
    return c


def _client() -> Mock:
    client = Mock()
    client.connected = True
    client.auth_status = AsyncMock(return_value={"state": "ok"})
    client.list_devices = AsyncMock(return_value=[{"sn": "A"}])
    client.list_solix_devices = AsyncMock(return_value=[])
    client.reset_connection = AsyncMock()
    return client


class FastRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cancel = Mock()
        patcher = patch.object(coord_mod, "async_call_later", return_value=self.cancel)
        self.call_later = patcher.start()
        self.addCleanup(patcher.stop)

    async def test_a_failed_poll_drops_the_socket_and_retries_soon(self):
        client = _client()
        client.list_devices.side_effect = EufySdkApiClientCommunicationError("timeout")
        c = _coordinator(client)

        with pytest.raises(UpdateFailed):
            await c._async_update_data()

        client.reset_connection.assert_awaited_once_with()
        self.call_later.assert_called_once()
        self.assertEqual(self.call_later.call_args.args[1], c._FAST_RETRY_S)

    async def test_an_error_reply_retries_soon_but_keeps_the_socket(self):
        client = _client()
        client.list_devices.side_effect = EufySdkApiClientError("devices.list failed")
        c = _coordinator(client)

        with pytest.raises(UpdateFailed):
            await c._async_update_data()

        client.reset_connection.assert_not_awaited()
        self.call_later.assert_called_once()
        self.assertEqual(self.call_later.call_args.args[1], c._FAST_RETRY_S)

    async def test_ready_during_real_snapshot_keeps_socket_but_retries_stale_poll(self):
        client = EufySdkApiClient("example.invalid", 3000, Mock())
        client._ws = Mock(closed=False)
        client.auth_status = AsyncMock(return_value={"state": "ok"})
        client.reset_connection = AsyncMock()

        async def rpc(cmd: str, **_kwargs: object) -> dict:
            self.assertEqual(cmd, "devices.list")
            client._dispatch_event({"event": "ready"})
            return {"devices": []}

        client.rpc = AsyncMock(side_effect=rpc)
        c = _coordinator(client)
        with pytest.raises(UpdateFailed, match="previous bridge session"):
            await c._async_update_data()
        client.reset_connection.assert_not_awaited()
        self.call_later.assert_called_once()

    async def test_a_bridge_still_booting_retries_soon_without_a_reset(self):
        client = _client()
        client.auth_status.return_value = {"state": "pending"}
        c = _coordinator(client)

        with pytest.raises(UpdateFailed):
            await c._async_update_data()

        self.call_later.assert_called_once()
        client.reset_connection.assert_not_awaited()

    async def test_repeated_failures_keep_a_single_pending_retry(self):
        client = _client()
        client.list_devices.side_effect = EufySdkApiClientCommunicationError("timeout")
        c = _coordinator(client)

        for _ in range(3):
            with pytest.raises(UpdateFailed):
                await c._async_update_data()

        self.call_later.assert_called_once()

    async def test_a_good_poll_cancels_the_pending_retry(self):
        client = _client()
        client.list_devices.side_effect = [EufySdkApiClientError("x"), [{"sn": "A"}]]
        c = _coordinator(client)
        with pytest.raises(UpdateFailed):
            await c._async_update_data()

        self.assertEqual(await c._async_update_data(), {"A": {"sn": "A"}})
        self.cancel.assert_called_once_with()
        self.assertIsNone(c._fast_retry_cancel)

    async def test_the_retry_fires_a_refresh_and_frees_the_slot(self):
        c = _coordinator(_client())
        c.async_request_refresh = Mock(return_value="refresh")
        c._fast_retry_cancel = self.cancel

        c._fast_retry_fire(None)

        c.hass.async_create_task.assert_called_once_with("refresh")
        self.assertIsNone(c._fast_retry_cancel)

    async def test_shutdown_cancels_a_pending_retry(self):
        c = _coordinator(_client())
        c._fast_retry_cancel = self.cancel
        with patch.object(
            coord_mod.DataUpdateCoordinator, "async_shutdown", AsyncMock()
        ):
            await c.async_shutdown()
        self.cancel.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
