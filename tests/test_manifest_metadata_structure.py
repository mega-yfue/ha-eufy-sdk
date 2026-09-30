# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, SLF001

import unittest
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, Mock

from custom_components.eufy_sdk.api import (
    EufySdkApiClient,
)
from custom_components.eufy_sdk.manifest_reads import normalize_properties

SN = "EXAMPLE-CAM-0001"
READ = {
    "accessor": "recordingQuality",
    "property": "recordingQuality",
    "type": "number",
    "kind": "enum",
    "values": [1, 2, 3],
    "writable": True,
}
METADATA = {
    "properties": [
        {"name": "recordingQuality", "type": "string", "raw": True},
        {"name": "alarmVolume", "type": "number", "writable": True, "writeOnly": True},
    ],
    "decodedProperties": {
        "details": [{"accessor": "camera", "reads": [READ]}],
    },
}
DEVICE = {
    "sn": SN,
    "model": "T8425",
    "capabilities": ["camera"],
    "state": {
        "recordingQuality": {"cur_mode": 0, "mode_0": {"quality": 2}},
        "battery": 75,
        "value": {"legacy": True},
    },
    "decodedState": {"camera": {"recordingQuality": 2}},
}


class MetadataClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = EufySdkApiClient("example.invalid", 3000, Mock())
        self.device = deepcopy(DEVICE)
        self.metadata = deepcopy(METADATA)
        self.metadata_calls = 0

        async def rpc(cmd: str, **_kwargs: Any) -> dict:
            if cmd == "devices.list":
                return {"devices": [deepcopy(self.device)]}
            self.assertEqual(cmd, "device.properties")
            self.metadata_calls += 1
            return deepcopy(self.metadata)

        self.client.rpc = AsyncMock(side_effect=rpc)


class MalformedMetadataTests(MetadataClientTests):
    async def test_non_object_capability_is_rejected(self):
        for detail in (None, "camera", []):
            with self.subTest(detail=detail):
                self.metadata["decodedProperties"]["details"] = [detail]
                self.client._invalidate_properties()
                self.assertEqual(await self.client.list_devices(), [self.device])
                self.assertNotIn(SN, self.client._property_cache)

    async def test_unusable_capability_accessor_is_rejected(self):
        for namespace in (None, "", 1):
            with self.subTest(namespace=namespace):
                self.metadata["decodedProperties"]["details"] = [
                    {"accessor": namespace, "reads": [READ]},
                ]
                self.client._invalidate_properties()
                self.assertEqual(await self.client.list_devices(), [self.device])
                self.assertNotIn(SN, self.client._property_cache)

    async def test_unusable_read_list_is_rejected(self):
        for reads in (None, {}, "reads"):
            with self.subTest(reads=reads):
                self.metadata["decodedProperties"]["details"] = [
                    {"accessor": "camera", "reads": reads},
                ]
                self.client._invalidate_properties()
                self.assertEqual(await self.client.list_devices(), [self.device])
                self.assertNotIn(SN, self.client._property_cache)

    async def test_non_object_read_is_rejected(self):
        for read in (None, "recordingQuality", []):
            with self.subTest(read=read):
                self.metadata["decodedProperties"]["details"] = [
                    {"accessor": "camera", "reads": [read]},
                ]
                self.client._invalidate_properties()
                self.assertEqual(await self.client.list_devices(), [self.device])
                self.assertNotIn(SN, self.client._property_cache)

    async def test_unusable_read_identities_are_rejected(self):
        for key in ("accessor", "property"):
            for value in (None, "", 1):
                with self.subTest(key=key, value=value):
                    self.metadata["decodedProperties"]["details"] = [
                        {"accessor": "camera", "reads": [{**READ, key: value}]},
                    ]
                    self.client._invalidate_properties()
                    self.assertEqual(await self.client.list_devices(), [self.device])
                    self.assertNotIn(SN, self.client._property_cache)

    async def test_bad_metadata_is_evicted_and_repaired_on_the_next_poll(self):
        self.metadata["decodedProperties"]["details"] = [
            {"accessor": "camera", "reads": [{"property": "recordingQuality"}]},
        ]
        self.assertEqual(await self.client.list_devices(), [self.device])
        self.assertNotIn(SN, self.client._property_cache)
        self.metadata = deepcopy(METADATA)
        device = (await self.client.list_devices())[0]
        self.assertEqual(self.metadata_calls, 2)
        self.assertEqual(device["state"]["recordingQuality"], 2)
        self.assertEqual(device["state"]["battery"], 75)
        self.assertEqual(device["state"]["value"], {"legacy": True})


class MetadataCompatibilityTests(MetadataClientTests):
    async def test_well_formed_unmatched_reads_do_not_guess_property_aliases(self):
        for details in (
            [],
            [{"accessor": "camera", "reads": []}],
            [{"accessor": "olderCamera", "reads": [READ]}],
        ):
            with self.subTest(details=details):
                self.client._invalidate_properties()
                self.metadata["decodedProperties"]["details"] = details
                device = (await self.client.list_devices())[0]
                self.assertEqual(device["state"]["value"], {"legacy": True})
                self.assertEqual(device["state"]["battery"], 75)
                if details and details[0]["reads"]:
                    self.assertIsNone(device["state"]["recordingQuality"])
                else:
                    self.assertEqual(device["state"], DEVICE["state"])

    async def test_empty_unbound_and_action_only_surfaces_are_accepted(self):
        for manifest, decoded in (
            ({"bound": False, "details": []}, {}),
            ({"details": [{"accessor": "ptz", "reads": []}]}, {"ptz": {}}),
        ):
            with self.subTest(manifest=manifest):
                self.client._invalidate_properties()
                self.metadata["decodedProperties"] = manifest
                self.device["decodedState"] = decoded
                self.assertEqual(
                    (await self.client.list_devices())[0]["state"], DEVICE["state"]
                )

    async def test_aliases_and_write_only_properties_keep_their_identities(self):
        self.metadata["decodedProperties"]["details"][0]["reads"] = [
            {**READ, "accessor": "activeQuality"},
        ]
        self.device["decodedState"] = {"camera": {"activeQuality": 2}}
        device = (await self.client.list_devices())[0]
        self.assertEqual(device["state"]["recordingQuality"], 2)
        self.assertEqual(device["state"]["value"], {"legacy": True})
        specs = await self.client.get_properties(SN)
        self.assertEqual(specs[0]["name"], "recordingQuality")
        self.assertEqual(specs[1], METADATA["properties"][1])
        self.assertEqual(self.metadata_calls, 1)

    async def test_alias_collisions_are_unknown_without_clearing_unrelated_raw(self):
        read = {**READ, "accessor": "value", "property": "shared"}
        self.metadata["decodedProperties"]["details"] = [
            {"accessor": "first", "reads": [read]},
            {"accessor": "second", "reads": [read]},
        ]
        self.metadata["properties"] = [{"name": "shared", "writable": True}]
        self.device["decodedState"] = {"first": {"value": 1}, "second": {"value": 2}}
        self.device["state"]["shared"] = {"storage": 2}
        device = (await self.client.list_devices())[0]
        self.assertIsNone(device["state"]["shared"])
        self.assertEqual(device["state"]["value"], {"legacy": True})
        self.assertFalse((await self.client.get_properties(SN))[0]["writable"])

    async def test_optional_descriptor_fields_and_null_values_are_accepted(self):
        self.metadata["decodedProperties"]["details"][0]["reads"] = [
            {"accessor": "recordingQuality", "property": "recordingQuality"},
        ]
        self.device["decodedState"]["camera"]["recordingQuality"] = None
        self.assertIsNone(
            (await self.client.list_devices())[0]["state"]["recordingQuality"]
        )

    async def test_newer_metadata_with_missing_snapshot_read_publishes_unknown(self):
        self.metadata["decodedProperties"]["details"][0]["reads"].append(
            {"accessor": "newReading", "property": "frameRate", "type": "number"}
        )
        self.assertIsNone((await self.client.list_devices())[0]["state"]["frameRate"])

    async def test_legacy_snapshot_does_not_request_or_validate_decoded_metadata(self):
        del self.device["decodedState"]
        self.metadata["decodedProperties"] = {"details": [None]}
        self.assertEqual(await self.client.list_devices(), [self.device])
        self.assertEqual(self.metadata_calls, 0)

    def test_write_only_metadata_stays_unchanged_when_reads_are_empty(self):
        self.metadata["decodedProperties"]["details"] = []
        self.assertEqual(
            normalize_properties(self.metadata)[1], METADATA["properties"][1]
        )
