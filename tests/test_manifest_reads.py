# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, PT027, SLF001

import asyncio
import unittest
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, Mock

from custom_components.eufy_sdk.api import (
    EufySdkApiClient,
    EufySdkApiClientCommunicationError,
    EufySdkApiClientError,
)
from custom_components.eufy_sdk.manifest_reads import (
    normalize_properties,
    normalize_snapshot,
    snapshot_signature,
)
from custom_components.eufy_sdk.select import EufySdkSelect

SN = "EXAMPLE-CAM-0001"
READ = {
    "accessor": "recordingQuality",
    "property": "recordingQuality",
    "type": "string",
    "kind": "enum",
    "values": [1, 2, 3],
    "labels": {"1": "HD (720P)", "2": "Full HD (1080P)", "3": "Max"},
    "writable": True,
}
METADATA = {
    "properties": [
        {"name": "recordingQuality", "type": "string", "raw": True},
        {"name": "alarmVolume", "type": "number", "writable": True, "writeOnly": True},
        {"name": "battery", "type": "number"},
    ],
    "decodedProperties": {
        "bound": True,
        "details": [
            {"capability": "camera", "accessor": "camera", "reads": [READ]},
        ],
    },
}
DEVICE = {
    "sn": SN,
    "model": "T8425",
    "capabilities": ["camera"],
    "state": {
        "recordingQuality": {"cur_mode": 0, "mode_0": {"quality": 2}},
        "battery": 75,
    },
    "decodedState": {"camera": {"recordingQuality": 2}},
}


class ManifestReadTests(unittest.TestCase):
    def test_decoded_enum_reaches_actual_select_without_changing_property_id(self):
        coordinator = Mock()
        coordinator.data = {SN: normalize_snapshot(DEVICE, METADATA)}
        spec = normalize_properties(METADATA)[0]
        entity = EufySdkSelect(coordinator, SN, spec)
        self.assertEqual(entity.current_option, "Full HD (1080P)")
        self.assertEqual(entity.options, ["HD (720P)", "Full HD (1080P)", "Max"])
        self.assertEqual(entity._prop, "recordingQuality")
        self.assertNotIn("raw", spec)
        self.assertEqual(coordinator.data[SN]["state"]["battery"], 75)
        self.assertIsInstance(DEVICE["state"]["recordingQuality"], dict)

    def test_unknown_or_invalid_getter_never_falls_back_to_structured_raw(self):
        for value in (None, {}, "2", True, 9, float("inf")):
            with self.subTest(value=value):
                device = deepcopy(DEVICE)
                device["decodedState"]["camera"]["recordingQuality"] = value
                self.assertIsNone(
                    normalize_snapshot(device, METADATA)["state"]["recordingQuality"]
                )
        device["decodedState"] = {}
        self.assertIsNone(
            normalize_snapshot(device, METADATA)["state"]["recordingQuality"]
        )

    def test_missing_null_or_invalid_decoded_read_does_not_use_raw_scalar(self):
        for decoded in (
            {},
            {"camera": {}},
            {"camera": {"recordingQuality": None}},
            {"camera": {"recordingQuality": 9}},
        ):
            with self.subTest(decoded=decoded):
                device = deepcopy(DEVICE)
                device["state"]["recordingQuality"] = 2
                device["decodedState"] = decoded
                self.assertIsNone(
                    normalize_snapshot(device, METADATA)["state"]["recordingQuality"]
                )

    def test_false_zero_empty_string_and_text_enum_survive(self):
        for kind, value in (
            ("bool", False),
            ("number", 0),
            ("string", ""),
            ("string", "auto"),
        ):
            with self.subTest(kind=kind, value=value):
                metadata = deepcopy(METADATA)
                metadata["decodedProperties"]["details"][0]["reads"] = [
                    {
                        "accessor": "recordingQuality",
                        "property": "recordingQuality",
                        "type": kind,
                    },
                ]
                device = deepcopy(DEVICE)
                device["decodedState"]["camera"]["recordingQuality"] = value
                device["state"]["recordingQuality"] = "legacy scalar"
                self.assertEqual(
                    normalize_snapshot(device, metadata)["state"]["recordingQuality"],
                    value,
                )

    def test_ambiguous_flat_property_is_unknown_and_read_only(self):
        metadata = deepcopy(METADATA)
        metadata["decodedProperties"]["details"].append(
            {
                "accessor": "other",
                "reads": [READ],
            }
        )
        self.assertIsNone(
            normalize_snapshot(DEVICE, metadata)["state"]["recordingQuality"]
        )
        self.assertFalse(normalize_properties(metadata)[0]["writable"])

    def test_legacy_and_write_only_metadata_are_preserved(self):
        legacy = {"state": {"battery": 75}}
        self.assertIs(normalize_snapshot(legacy, METADATA), legacy)
        self.assertIs(
            normalize_properties({"properties": METADATA["properties"]}),
            METADATA["properties"],
        )
        self.assertEqual(normalize_properties(METADATA)[1], METADATA["properties"][1])

    def test_signature_ignores_values_but_tracks_model_and_read_shape(self):
        device = deepcopy(DEVICE)
        device["decodedState"]["camera"]["recordingQuality"] = 3
        self.assertEqual(snapshot_signature(device), snapshot_signature(DEVICE))
        device["decodedState"]["camera"]["newRead"] = None
        self.assertNotEqual(snapshot_signature(device), snapshot_signature(DEVICE))
        device = {**DEVICE, "model": "other"}
        self.assertNotEqual(snapshot_signature(device), snapshot_signature(DEVICE))


class MetadataCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = EufySdkApiClient("example.invalid", 3000, Mock())
        self.device = deepcopy(DEVICE)
        self.metadata_calls = 0
        self.fail_metadata = False

        async def rpc(cmd: str, **_kwargs: Any) -> dict:
            if cmd == "devices.list":
                return {"devices": [deepcopy(self.device)]}
            self.assertEqual(cmd, "device.properties")
            self.metadata_calls += 1
            if self.fail_metadata:
                message = "metadata unavailable"
                raise EufySdkApiClientError(message)
            return deepcopy(METADATA)

        self.client.rpc = AsyncMock(side_effect=rpc)

    async def test_one_manifest_shared_by_setup_and_polls_with_fresh_values(self):
        await self.client.list_devices()
        await self.client.get_properties(SN)
        self.device["decodedState"]["camera"]["recordingQuality"] = 3
        devices = await self.client.list_devices()
        self.assertEqual(devices[0]["state"]["recordingQuality"], 3)
        self.assertEqual(self.metadata_calls, 1)

    async def test_legacy_poll_does_not_request_metadata(self):
        del self.device["decodedState"]
        self.assertEqual(await self.client.list_devices(), [self.device])
        self.assertEqual(self.metadata_calls, 0)

    async def test_read_shape_and_model_changes_refresh_metadata(self):
        await self.client.list_devices()
        self.device["decodedState"]["camera"]["newRead"] = None
        await self.client.list_devices()
        self.device["model"] = "other"
        await self.client.list_devices()
        self.assertEqual(self.metadata_calls, 3)

    async def test_failed_metadata_is_retried_instead_of_using_old_mapping(self):
        await self.client.list_devices()
        self.device["model"] = "other"
        self.fail_metadata = True
        with self.assertRaises(EufySdkApiClientError):
            await self.client.list_devices()
        self.assertNotIn(SN, self.client._property_cache)
        self.fail_metadata = False
        await self.client.list_devices()
        self.assertEqual(self.metadata_calls, 3)

    async def test_close_discards_metadata(self):
        await self.client.list_devices()
        await self.client.close()
        await self.client.list_devices()
        self.assertEqual(self.metadata_calls, 2)

    async def test_concurrent_polls_share_one_metadata_request(self):
        await asyncio.gather(self.client.list_devices(), self.client.list_devices())
        self.assertEqual(self.metadata_calls, 1)

    async def test_inflight_metadata_from_previous_session_is_rejected(self):
        async def rpc(_cmd: str, **_kwargs: Any) -> dict:
            self.client._invalidate_properties()
            return deepcopy(METADATA)

        self.client.rpc = AsyncMock(side_effect=rpc)
        with self.assertRaises(EufySdkApiClientCommunicationError):
            await self.client.get_properties(SN)
        self.assertNotIn(SN, self.client._property_cache)

    async def test_removed_devices_are_evicted(self):
        await self.client.list_devices()
        self.client.rpc = AsyncMock(return_value={"devices": []})
        await self.client.list_devices()
        self.assertNotIn(SN, self.client._property_cache)

    async def test_ready_event_invalidates_before_forwarding(self):
        await self.client.list_devices()
        observed = []
        self.client._on_event = lambda _event: observed.append(
            bool(self.client._property_cache)
        )
        self.client._dispatch_event({"event": "ready"})
        self.assertEqual(observed, [False])
        await self.client.list_devices()
        self.assertEqual(self.metadata_calls, 2)

    async def test_session_change_during_snapshot_rejects_the_old_values(self):
        original = self.client.rpc

        async def rpc(cmd: str, **kwargs: Any) -> dict:
            reply = await original(cmd, **kwargs)
            if cmd == "devices.list":
                self.client._invalidate_properties()
            return reply

        self.client.rpc = AsyncMock(side_effect=rpc)
        with self.assertRaises(EufySdkApiClientCommunicationError):
            await self.client.list_devices()

    async def test_decoded_snapshot_without_metadata_preserves_raw_and_refetches(self):
        original = self.client.rpc

        async def rpc(cmd: str, **kwargs: Any) -> dict:
            reply = await original(cmd, **kwargs)
            if cmd == "device.properties":
                reply.pop("decodedProperties")
            return reply

        self.client.rpc = AsyncMock(side_effect=rpc)
        self.assertEqual(await self.client.list_devices(), [self.device])

        self.assertNotIn(SN, self.client._property_cache)
        self.client.rpc = original
        self.assertEqual(
            (await self.client.list_devices())[0]["state"]["recordingQuality"], 2
        )
        self.assertEqual(self.metadata_calls, 2)
