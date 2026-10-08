# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, PT027, SLF001

import asyncio
import unittest
from collections.abc import AsyncIterator
from copy import deepcopy
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.eufy_sdk import api as api_mod
from custom_components.eufy_sdk import coordinator as coord_mod
from custom_components.eufy_sdk.api import (
    EufySdkApiClient,
    EufySdkApiClientAuthenticationError,
    EufySdkApiClientCommunicationError,
    EufySdkApiClientError,
)

METADATA = {
    "properties": [{"name": "recordingQuality", "type": "string", "raw": True}],
    "decodedProperties": {
        "details": [
            {
                "accessor": "camera",
                "reads": [
                    {
                        "accessor": "activeQuality",
                        "property": "recordingQuality",
                        "type": "number",
                    }
                ],
            }
        ]
    },
}

LOGGER = "custom_components.eufy_sdk.api"
RPC_DEADLINE = 15


class MetadataTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = EufySdkApiClient("example.invalid", 3000, Mock())
        self.ws = Mock(closed=False)
        self.ws.send_json = AsyncMock(side_effect=self.send)
        self.client._ws = self.ws
        self.requests = []
        self.timeout_metadata = True
        self.probe = {"ok": True, "auth": {"state": "ok"}}
        self.probe_hook = None
        self.metadata_hook = None
        self.stall_probe = False
        self.fail_probe_send = False
        self.probe_started = asyncio.Event()
        self.devices = [
            {
                "sn": sn,
                "model": "EXAMPLE",
                "capabilities": ["camera"],
                "state": {"recordingQuality": "raw"},
                "decodedState": {"camera": {"activeQuality": 2}},
            }
            for sn in ("EXAMPLE-BAD", "EXAMPLE-GOOD")
        ]
        real_timeout = asyncio.timeout
        self.deadlines = []

        def short_timeout(delay: float) -> asyncio.Timeout:
            self.deadlines.append(delay)
            return real_timeout(0.02)

        patcher = patch.object(api_mod.asyncio, "timeout", side_effect=short_timeout)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def send(self, request: dict):
        self.requests.append(request)
        cmd = request["cmd"]
        if cmd == "devices.list":
            reply = {"ok": True, "devices": deepcopy(self.devices)}
        elif cmd == "device.properties":
            if request["sn"] == "EXAMPLE-BAD" and self.timeout_metadata:
                if self.metadata_hook:
                    self.metadata_hook()
                return
            reply = {"ok": True, **deepcopy(METADATA)}
        elif cmd == "auth.status":
            self.probe_started.set()
            if self.probe_hook:
                self.probe_hook()
            if self.fail_probe_send:
                msg = "synthetic send failure"
                raise OSError(msg)
            if self.stall_probe:
                await asyncio.Future()
            reply = self.probe
            if reply is None:
                return
        elif cmd == "solix.devices":
            reply = {"ok": True, "devices": []}
        else:
            self.fail(f"Unexpected command: {cmd}")
        self.client._pending[request["id"]].set_result(reply)

    async def test_real_timeout_preserves_raw_continues_and_repairs_next_poll(self):
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            first = await self.client.list_devices()
            second = await self.client.list_devices()
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(first[0], self.devices[0])
        self.assertEqual(second[1]["state"]["recordingQuality"], 2)
        self.assertEqual(set(self.client._property_cache), {"EXAMPLE-GOOD"})
        self.assertFalse(self.client._pending)
        timed_out_id = next(
            r["id"] for r in self.requests if r["cmd"] == "device.properties"
        )
        self.assertNotIn(timed_out_id, self.client._pending)
        self.timeout_metadata = False
        with self.assertNoLogs(LOGGER, level="WARNING"):
            repaired = await self.client.list_devices()
        self.assertEqual(repaired[0]["state"]["recordingQuality"], 2)
        self.assertNotIn("EXAMPLE-BAD", self.client._invalid_metadata_devices)
        self.assertEqual(sum(r["cmd"] == "auth.status" for r in self.requests), 2)
        self.assertTrue(all(deadline == RPC_DEADLINE for deadline in self.deadlines))

    async def test_invalid_or_unready_probe_never_returns_partial_success(self):
        for probe, error in (
            (
                {"ok": True, "auth": {"state": "require_2fa"}},
                EufySdkApiClientAuthenticationError,
            ),
            (
                {"ok": True, "auth": {"state": "require_captcha"}},
                EufySdkApiClientAuthenticationError,
            ),
            ({"ok": True, "auth": {"state": "pending"}}, EufySdkApiClientError),
            ({"ok": True, "auth": []}, EufySdkApiClientCommunicationError),
            ({"ok": True}, EufySdkApiClientCommunicationError),
            (
                {"ok": True, "auth": {"state": "unexpected"}},
                EufySdkApiClientCommunicationError,
            ),
            ({"ok": False, "error": "rejected"}, EufySdkApiClientError),
            ({"ok": 1, "auth": {"state": "ok"}}, EufySdkApiClientCommunicationError),
        ):
            with self.subTest(probe=probe):
                self.probe = probe
                with self.assertRaises(error) as raised:
                    await self.client.list_devices()
                self.assertIs(type(raised.exception), error)
                self.assertFalse(self.client._pending)
                self.assertFalse(self.client._property_cache)

    async def test_ready_disconnect_and_replacement_during_metadata_or_probe(self):
        for phase in ("metadata_hook", "probe_hook"):
            for transition in ("ready", "disconnect", "replace"):
                with self.subTest(phase=phase, transition=transition):
                    self.client._ws = self.ws
                    self.ws.closed = False
                    self.metadata_hook = self.probe_hook = None
                    self.requests.clear()

                    def change(transition: str = transition) -> None:
                        if transition == "ready":
                            self.client._dispatch_event({"event": "ready"})
                        elif transition == "disconnect":
                            self.ws.closed = True
                        else:
                            self.client._ws = Mock(closed=False)

                    setattr(self, phase, change)
                    with self.assertRaises(EufySdkApiClientError) as raised:
                        await self.client.list_devices()
                    expected = (
                        EufySdkApiClientError
                        if transition == "ready"
                        else EufySdkApiClientCommunicationError
                    )
                    self.assertIs(type(raised.exception), expected)
                    self.assertFalse(self.client._pending)
                    self.assertFalse(self.client._property_cache)
                    self.assertEqual(
                        sum(r["cmd"] == "auth.status" for r in self.requests),
                        int(phase == "probe_hook"),
                    )

    async def test_probe_total_deadline_bounds_stalled_send_and_cleans_pending(self):
        self.stall_probe = True
        with self.assertRaises(EufySdkApiClientCommunicationError):
            await self.client.list_devices()
        self.assertTrue(self.probe_started.is_set())
        self.assertFalse(self.client._pending)

    async def test_probe_send_failure_cleans_pending(self):
        self.fail_probe_send = True
        with self.assertRaises(EufySdkApiClientCommunicationError):
            await self.client.list_devices()
        self.assertFalse(self.client._pending)

    async def test_external_cancellation_during_probe_send_propagates(self):
        self.stall_probe = True
        task = asyncio.create_task(self.client.list_devices())
        await self.probe_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.client._pending)

    async def test_public_property_timeout_does_not_probe(self):
        with self.assertRaises(EufySdkApiClientCommunicationError):
            await self.client.get_properties("EXAMPLE-BAD")
        self.assertEqual([r["cmd"] for r in self.requests], ["device.properties"])
        self.assertFalse(self.client._pending)

    async def test_shared_rpc_send_failure_and_reply_cancellation_clean_pending(self):
        self.ws.send_json.side_effect = OSError("synthetic send failure")
        with self.assertRaises(OSError):
            await self.client.rpc("device.properties")
        self.assertFalse(self.client._pending)
        sent = asyncio.Event()

        async def no_reply(_request: dict) -> None:
            sent.set()

        self.ws.send_json.side_effect = no_reply
        task = asyncio.create_task(self.client.rpc("device.properties"))
        await sent.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.client._pending)

    async def test_wedged_probe_retains_real_coordinator_reset_and_retry(self):
        self.probe = None
        self.client.auth_status = AsyncMock(return_value={"state": "ok"})
        self.client.reset_connection = AsyncMock()
        coordinator = object.__new__(coord_mod.EufySdkDataUpdateCoordinator)
        coordinator.hass = Mock()
        coordinator.config_entry = Mock()
        coordinator.config_entry.runtime_data.client = self.client
        coordinator._fast_retry_cancel = None
        with (
            patch.object(coord_mod, "async_call_later") as later,
            self.assertRaises(UpdateFailed),
        ):
            await coordinator._async_update_data()
        self.client.reset_connection.assert_awaited_once_with()
        later.assert_called_once()
        self.assertFalse(self.client._pending)

    async def test_late_metadata_reply_is_ignored_by_receive_loop(self):
        with self.assertLogs(LOGGER, level="WARNING"):
            await self.client.list_devices()
        original = next(r for r in self.requests if r["cmd"] == "device.properties")
        consumed = asyncio.Event()
        frame = Mock(type=aiohttp.WSMsgType.TEXT)
        frame.json.return_value = {
            "id": original["id"],
            "ok": True,
            **deepcopy(METADATA),
        }

        async def frames() -> AsyncIterator[Mock]:
            yield frame
            consumed.set()
            await asyncio.Future()

        self.ws.__aiter__ = Mock(return_value=frames())
        self.client._closing = True
        receiver = asyncio.create_task(self.client._receive_loop())
        try:
            await consumed.wait()
            self.assertNotIn("EXAMPLE-BAD", self.client._property_cache)
            self.assertFalse(self.client._pending)
            self.assertEqual(set(self.client._property_cache), {"EXAMPLE-GOOD"})
        finally:
            receiver.cancel()
            await receiver

    async def test_responsive_probe_preserves_real_coordinator_poll_without_reset(self):
        self.client.reset_connection = AsyncMock()
        coordinator = object.__new__(coord_mod.EufySdkDataUpdateCoordinator)
        coordinator.hass = Mock()
        coordinator.config_entry = Mock()
        coordinator.config_entry.runtime_data.client = self.client
        coordinator._fast_retry_cancel = None
        with (
            patch.object(coord_mod, "async_call_later") as later,
            self.assertLogs(LOGGER, level="WARNING"),
        ):
            result = await coordinator._async_update_data()
        self.assertEqual(result["EXAMPLE-BAD"], self.devices[0])
        self.assertEqual(result["EXAMPLE-GOOD"]["state"]["recordingQuality"], 2)
        self.client.reset_connection.assert_not_awaited()
        later.assert_not_called()
        self.assertFalse(self.client._pending)

    async def test_send_timeout_is_not_a_metadata_reply_timeout(self):
        original_send = self.send

        async def send_timeout(request: dict) -> None:
            if request["cmd"] == "device.properties":
                raise TimeoutError
            await original_send(request)

        self.ws.send_json.side_effect = send_timeout
        with self.assertRaises(TimeoutError):
            await self.client.list_devices()
        self.assertFalse(self.client._pending)
        self.assertEqual([r["cmd"] for r in self.requests], ["devices.list"])
