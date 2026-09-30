# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, PT027, SLF001

import logging
import unittest
from collections import Counter
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, Mock

from custom_components.eufy_sdk.api import (
    EufySdkApiClient,
    EufySdkApiClientAuthenticationError,
    EufySdkApiClientCommunicationError,
    EufySdkApiClientError,
)

LOGGER = "custom_components.eufy_sdk.api"
FIRST_SN = "EXAMPLE-CAM-FIRST"
BAD_SN = "EXAMPLE-CAM-BAD"
LAST_SN = "EXAMPLE-CAM-LAST"
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


class DeviceIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = EufySdkApiClient("example.invalid", 3000, Mock())
        self.devices = [
            {
                "sn": sn,
                "model": "T8425",
                "capabilities": ["camera"],
                "state": {
                    "recordingQuality": {"storage": value},
                    "battery": 75,
                    "value": {"legacy": True},
                },
                "decodedState": {"camera": {"activeQuality": value}},
            }
            for sn, value in ((FIRST_SN, 1), (BAD_SN, 2), (LAST_SN, 3))
        ]
        self.metadata = {sn: deepcopy(METADATA) for sn in (FIRST_SN, BAD_SN, LAST_SN)}
        self.metadata[BAD_SN]["decodedProperties"] = {"details": [None]}
        self.metadata_calls = Counter()
        self.failures: dict[str, Exception] = {}
        self.snapshots: list[dict[str, Any]] = []

        async def rpc(cmd: str, **kwargs: Any) -> dict:
            if cmd == "devices.list":
                self.snapshots = deepcopy(self.devices)
                return {"devices": self.snapshots}
            self.assertEqual(cmd, "device.properties")
            sn = kwargs["sn"]
            self.metadata_calls[sn] += 1
            if sn in self.failures:
                raise self.failures[sn]
            return deepcopy(self.metadata[sn])

        self.client.rpc = AsyncMock(side_effect=rpc)

    async def assert_fleet_isolated(self):
        devices = await self.client.list_devices()
        self.assertEqual(
            [device["sn"] for device in devices], [FIRST_SN, BAD_SN, LAST_SN]
        )
        self.assertEqual(devices[0]["state"]["recordingQuality"], 1)
        self.assertEqual(devices[2]["state"]["recordingQuality"], 3)
        self.assertIs(devices[1], self.snapshots[1])
        self.assertEqual(devices[1], self.devices[1])
        self.assertEqual(self.snapshots, self.devices)
        for device in devices:
            self.assertEqual(device["state"]["battery"], 75)
            self.assertEqual(device["state"]["value"], {"legacy": True})

    async def test_bad_middle_device_preserves_raw_and_continues_healthy_fleet(self):
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            await self.assert_fleet_isolated()
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].name, LOGGER)
        self.assertEqual(logs.records[0].levelno, logging.WARNING)
        self.assertIn(BAD_SN, logs.records[0].getMessage())
        self.assertEqual(
            self.metadata_calls, Counter({FIRST_SN: 1, BAD_SN: 1, LAST_SN: 1})
        )
        self.assertEqual(set(self.client._property_cache), {FIRST_SN, LAST_SN})

    async def test_repeated_polls_refetch_only_bad_metadata_and_warn_once(self):
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            for _ in range(3):
                await self.assert_fleet_isolated()
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(
            self.metadata_calls, Counter({FIRST_SN: 1, BAD_SN: 3, LAST_SN: 1})
        )

    async def test_warnings_are_suppressed_separately_for_each_bad_serial(self):
        self.metadata[LAST_SN]["decodedProperties"] = {"details": [None]}
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            for _ in range(2):
                devices = await self.client.list_devices()
                self.assertEqual(devices[0]["state"]["recordingQuality"], 1)
                self.assertIs(devices[1], self.snapshots[1])
                self.assertIs(devices[2], self.snapshots[2])
        self.assertEqual(len(logs.records), 2)
        for sn in (BAD_SN, LAST_SN):
            self.assertEqual(
                sum(sn in record.getMessage() for record in logs.records), 1
            )
        self.assertEqual(
            self.metadata_calls, Counter({FIRST_SN: 1, BAD_SN: 2, LAST_SN: 2})
        )

    async def test_session_invalidation_preserves_warning_suppression(self):
        with self.assertLogs(LOGGER, level="WARNING"):
            await self.assert_fleet_isolated()
        self.client._invalidate_properties()
        with self.assertNoLogs(LOGGER, level="WARNING"):
            await self.assert_fleet_isolated()
        self.assertEqual(
            self.metadata_calls, Counter({FIRST_SN: 2, BAD_SN: 2, LAST_SN: 2})
        )

    async def test_repair_clears_warning_suppression_before_later_bad_metadata(self):
        with self.assertLogs(LOGGER, level="WARNING"):
            await self.assert_fleet_isolated()
        self.metadata[BAD_SN] = deepcopy(METADATA)
        with self.assertNoLogs(LOGGER, level="WARNING"):
            repaired = await self.client.list_devices()
            self.assertEqual(repaired[1]["state"]["recordingQuality"], 2)
            await self.client.list_devices()
        self.assertEqual(self.metadata_calls[BAD_SN], 2)
        self.metadata[BAD_SN]["decodedProperties"] = {"details": [None]}
        self.devices[1]["model"] = "CHANGED-MODEL"
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            await self.assert_fleet_isolated()
            await self.assert_fleet_isolated()
        self.assertEqual(len(logs.records), 1)
        self.assertIn(BAD_SN, logs.records[0].getMessage())
        self.assertEqual(
            self.metadata_calls, Counter({FIRST_SN: 1, BAD_SN: 4, LAST_SN: 1})
        )

    async def test_removed_device_clears_warning_suppression(self):
        with self.assertLogs(LOGGER, level="WARNING"):
            await self.assert_fleet_isolated()
        removed = self.devices.pop(1)
        with self.assertNoLogs(LOGGER, level="WARNING"):
            devices = await self.client.list_devices()
        self.assertEqual([device["sn"] for device in devices], [FIRST_SN, LAST_SN])
        self.devices.insert(1, removed)
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            await self.assert_fleet_isolated()
        self.assertEqual(len(logs.records), 1)
        self.assertIn(BAD_SN, logs.records[0].getMessage())
        self.assertEqual(
            self.metadata_calls, Counter({FIRST_SN: 1, BAD_SN: 2, LAST_SN: 1})
        )

    async def test_legacy_snapshot_clears_warning_suppression_without_metadata_rpc(
        self,
    ):
        with self.assertLogs(LOGGER, level="WARNING"):
            await self.assert_fleet_isolated()
        decoded = self.devices[1].pop("decodedState")
        with self.assertNoLogs(LOGGER, level="WARNING"):
            await self.assert_fleet_isolated()
        self.assertEqual(self.metadata_calls[BAD_SN], 1)
        self.devices[1]["decodedState"] = decoded
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            await self.assert_fleet_isolated()
        self.assertEqual(len(logs.records), 1)
        self.assertIn(BAD_SN, logs.records[0].getMessage())
        self.assertEqual(
            self.metadata_calls, Counter({FIRST_SN: 1, BAD_SN: 2, LAST_SN: 1})
        )

    async def test_property_rpc_errors_propagate_unchanged_for_healthy_and_bad_devices(
        self,
    ):
        for error_type in (
            EufySdkApiClientCommunicationError,
            EufySdkApiClientAuthenticationError,
            EufySdkApiClientError,
        ):
            for sn in (FIRST_SN, BAD_SN):
                with self.subTest(error_type=error_type, sn=sn):
                    self.client._invalidate_properties()
                    failure = error_type("property RPC failed")
                    self.failures = {sn: failure}
                    with (
                        self.assertNoLogs(LOGGER, level="WARNING"),
                        self.assertRaises(error_type) as raised,
                    ):
                        await self.client.list_devices()
                    self.assertIs(raised.exception, failure)
                    self.assertNotIn(sn, self.client._property_cache)

    async def test_bad_device_rpc_error_after_malformed_metadata_is_not_suppressed(
        self,
    ):
        with self.assertLogs(LOGGER, level="WARNING"):
            await self.assert_fleet_isolated()
        for error_type in (
            EufySdkApiClientCommunicationError,
            EufySdkApiClientAuthenticationError,
            EufySdkApiClientError,
        ):
            with self.subTest(error_type=error_type):
                failure = error_type("property RPC failed after malformed metadata")
                self.failures[BAD_SN] = failure
                with (
                    self.assertNoLogs(LOGGER, level="WARNING"),
                    self.assertRaises(error_type) as raised,
                ):
                    await self.client.list_devices()
                self.assertIs(raised.exception, failure)
                self.assertEqual(set(self.client._property_cache), {FIRST_SN, LAST_SN})
                self.failures.clear()
                with self.assertNoLogs(LOGGER, level="WARNING"):
                    await self.assert_fleet_isolated()

    async def test_session_change_during_bad_metadata_rpc_still_rejects_snapshot(self):
        original = self.client.rpc

        async def rpc(cmd: str, **kwargs: Any) -> dict:
            reply = await original(cmd, **kwargs)
            if cmd == "device.properties" and kwargs["sn"] == BAD_SN:
                self.client._invalidate_properties()
            return reply

        self.client.rpc = AsyncMock(side_effect=rpc)
        with (
            self.assertNoLogs(LOGGER, level="WARNING"),
            self.assertRaisesRegex(
                EufySdkApiClientCommunicationError,
                "property metadata belongs to a previous bridge session",
            ),
        ):
            await self.client.list_devices()
        self.assertEqual(self.metadata_calls[LAST_SN], 0)
        self.assertFalse(self.client._property_cache)

    async def test_session_change_during_list_still_rejects_partially_raw_fleet(self):
        original = self.client.rpc

        async def rpc(cmd: str, **kwargs: Any) -> dict:
            reply = await original(cmd, **kwargs)
            if cmd == "devices.list":
                self.client._invalidate_properties()
            return reply

        self.client.rpc = AsyncMock(side_effect=rpc)
        with (
            self.assertLogs(LOGGER, level="WARNING"),
            self.assertRaisesRegex(
                EufySdkApiClientCommunicationError,
                "device snapshot belongs to a previous bridge session",
            ),
        ):
            await self.client.list_devices()
        self.assertEqual(self.metadata_calls[LAST_SN], 1)
