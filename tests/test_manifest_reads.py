# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, PT027, SLF001

import asyncio
import unittest
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from custom_components.eufy_sdk import number
from custom_components.eufy_sdk.api import (
    EufySdkApiClient,
    EufySdkApiClientCommunicationError,
    EufySdkApiClientError,
)
from custom_components.eufy_sdk.entity import classify
from custom_components.eufy_sdk.manifest_reads import (
    normalize_properties,
    normalize_snapshot,
    snapshot_signature,
)
from custom_components.eufy_sdk.select import EufySdkSelect
from custom_components.eufy_sdk.sensor import EufySdkPropertySensor

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


class DecodedSecondsTests(unittest.IsolatedAsyncioTestCase):
    def _fixture(
        self, model: str = "T9000", namespace: str = "station", value: object = 90
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        metadata = {
            "properties": [
                {
                    "name": "snoozeDuration",
                    "type": "string",
                    "raw": True,
                    "unexposed": True,
                },
                {"name": "battery", "type": "number"},
            ],
            "decodedProperties": {
                "details": [
                    {
                        "accessor": namespace,
                        "reads": [
                            {
                                "property": "snoozeDuration",
                                "accessor": "snoozeSeconds",
                                "type": "string",
                                "kind": "seconds",
                                "unit": "s",
                                "writable": True,
                            }
                        ],
                    }
                ]
            },
        }
        device = {
            "sn": SN,
            "name": "Example device",
            "model": model,
            "capabilities": [namespace],
            "state": {"snoozeDuration": 900, "battery": 75},
            "decodedState": {namespace: {"snoozeSeconds": value}},
        }
        return metadata, device

    async def _setup_numbers(
        self, metadata: dict[str, Any], device: dict[str, Any]
    ) -> tuple[list[number.EufySdkNumber], Mock, dict[str, Any]]:
        original_metadata, original_device = deepcopy(metadata), deepcopy(device)
        coordinator = Mock()
        coordinator.data = {SN: normalize_snapshot(device, metadata)}
        coordinator.async_request_refresh = AsyncMock()
        entry = Mock()
        entry.runtime_data.coordinator = coordinator
        entry.runtime_data.properties = {SN: normalize_properties(metadata)}
        coordinator.config_entry = entry
        added: list[number.EufySdkNumber] = []
        with patch.object(number, "solix_devices_with", return_value=[]):
            await number.async_setup_entry(Mock(), entry, added.extend)
        self.assertEqual(entry.runtime_data.client.mock_calls, [])
        coordinator.async_request_refresh.assert_not_called()
        self.assertEqual(metadata, original_metadata)
        self.assertEqual(device, original_device)
        self.assertEqual(coordinator.data[SN]["state"]["battery"], 75)
        self.assertEqual(
            entry.runtime_data.properties[SN][1], metadata["properties"][1]
        )
        return added, coordinator, entry.runtime_data.properties[SN][0]

    async def test_seconds_route_through_number_setup_for_synthetic_device_families(
        self,
    ):
        # These are consumer fixtures, not claims about device or transport support.
        for model, namespace in (
            ("T9000", "station"),
            ("T8010", "station"),
            ("T8030", "station"),
            ("T8425", "camera"),
        ):
            with self.subTest(model=model):
                metadata, device = self._fixture(model, namespace)
                metadata["properties"][0].update({"min": 15, "max": 7200})
                added, _, spec = await self._setup_numbers(metadata, device)
                self.assertEqual(len(added), 1)
                entity = added[0]
                self.assertIsInstance(entity, number.EufySdkNumber)
                self.assertEqual(spec["type"], "number")
                self.assertNotIn("raw", spec)
                self.assertNotIn("unexposed", spec)
                self.assertEqual(entity._prop, "snoozeDuration")
                self.assertEqual(entity.unique_id, f"{SN}_snoozeDuration")
                self.assertEqual(entity.name, "Snooze Duration")
                self.assertEqual(entity.native_value, 90.0)
                self.assertEqual(entity.native_unit_of_measurement, "s")
                self.assertEqual(
                    (entity.native_min_value, entity.native_max_value), (15, 7200)
                )

    async def test_real_snooze_seconds_number_sends_numeric_device_set(self):
        metadata, device = self._fixture("T8425", "motion", 3600)
        metadata["properties"][0] = {
            "name": "snoozeTime",
            "type": "string",
            "writable": True,
        }
        metadata["decodedProperties"]["details"][0]["reads"][0] = {
            "property": "snoozeTime",
            "accessor": "snoozeTime",
            "type": "string",
            "kind": "seconds",
            "writable": True,
        }
        device["state"] = {"snoozeTime": "encoded-config", "battery": 75}
        device["decodedState"] = {"motion": {"snoozeTime": 3600}}
        added, coordinator, _ = await self._setup_numbers(metadata, device)
        self.assertEqual(len(added), 1)
        client = EufySdkApiClient("example.invalid", 3000, Mock())
        client.rpc = AsyncMock(return_value={"ok": True})
        coordinator.config_entry.runtime_data.client = client
        entity = added[0]
        entity.hass = Mock()
        entity.async_write_ha_state = Mock()
        for value in (3600.0, 0.0):
            with (
                self.subTest(value=value),
                patch("custom_components.eufy_sdk.entity.async_call_later"),
            ):
                client.rpc.reset_mock()
                await entity.async_set_native_value(value)
                client.rpc.assert_awaited_once_with(
                    "device.set", sn=SN, name="snoozeTime", value=int(value)
                )
                self.assertIs(type(client.rpc.await_args.kwargs["value"]), int)

    async def test_seconds_preserve_finite_values_and_existing_default_bounds(self):
        for value in (0, 120, 1.5, -0.5, 100000):
            with self.subTest(value=value):
                added, _, _ = await self._setup_numbers(*self._fixture(value=value))
                self.assertEqual(len(added), 1)
                self.assertEqual(added[0].native_value, float(value))
                # Presentation defaults neither clamp values nor qualify vendor limits.
                self.assertEqual(
                    (added[0].native_min_value, added[0].native_max_value), (0, 86400)
                )

    async def test_only_exact_true_writable_seconds_route_to_numbers(self):
        for writable in (False, None, "true"):
            with self.subTest(writable=writable):
                metadata, device = self._fixture(value=1.5)
                read = metadata["decodedProperties"]["details"][0]["reads"][0]
                if writable is None:
                    read.pop("writable")
                else:
                    read["writable"] = writable
                added, coordinator, spec = await self._setup_numbers(metadata, device)
                self.assertEqual(added, [])
                self.assertFalse(spec["writable"])
                self.assertEqual(spec["type"], "number")
                self.assertEqual(classify(spec), "sensor")
                sensor = EufySdkPropertySensor(coordinator, SN, spec)
                self.assertEqual(sensor.native_value, 1.5)
                self.assertEqual(sensor._prop, "snoozeDuration")
                self.assertEqual(sensor.unique_id, f"{SN}_snoozeDuration")
                self.assertEqual(sensor.name, "Snooze Duration")
                self.assertEqual(sensor.native_unit_of_measurement, "s")

    async def test_invalid_seconds_readings_remain_unknown_despite_raw_number(self):
        for value in (
            None,
            True,
            False,
            "120",
            "",
            [],
            {"value": 120},
            {"error": "unavailable"},
            float("inf"),
            float("-inf"),
            float("nan"),
            10**400,
        ):
            with self.subTest(value=value):
                added, coordinator, _ = await self._setup_numbers(
                    *self._fixture(value=value)
                )
                self.assertEqual(len(added), 1)
                self.assertIsNone(added[0].native_value)
                self.assertIsNone(coordinator.data[SN]["state"]["snoozeDuration"])

    async def test_missing_or_malformed_decoded_surfaces_remain_unknown(self):
        for decoded in (
            None,
            [],
            {},
            {"station": None},
            {"station": []},
            {"station": "120"},
            {"station": {}},
            {"station": {"otherAccessor": 120}},
            {"otherNamespace": {"snoozeSeconds": 120}},
        ):
            with self.subTest(decoded=decoded):
                metadata, device = self._fixture()
                device["decodedState"] = decoded
                added, coordinator, _ = await self._setup_numbers(metadata, device)
                self.assertEqual(len(added), 1)
                self.assertIsNone(added[0].native_value)
                self.assertIsNone(coordinator.data[SN]["state"]["snoozeDuration"])

    async def test_duplicate_seconds_namespaces_are_unknown_and_read_only(self):
        metadata, device = self._fixture()
        other = deepcopy(metadata["decodedProperties"]["details"][0])
        other["accessor"] = "other"
        metadata["decodedProperties"]["details"].append(other)
        device["decodedState"]["other"] = {"snoozeSeconds": 30}
        added, coordinator, spec = await self._setup_numbers(metadata, device)
        self.assertEqual(added, [])
        self.assertFalse(spec["writable"])
        self.assertEqual(classify(spec), "sensor")
        self.assertIsNone(EufySdkPropertySensor(coordinator, SN, spec).native_value)

    def test_unit_or_numeric_sample_alone_does_not_imply_decoded_seconds(self):
        for kind in (None, "duration"):
            with self.subTest(kind=kind):
                metadata, device = self._fixture()
                read = metadata["decodedProperties"]["details"][0]["reads"][0]
                if kind is None:
                    read.pop("kind")
                else:
                    read["kind"] = kind
                spec = normalize_properties(metadata)[0]
                self.assertEqual(spec["type"], "string")
                self.assertEqual(classify(spec), "sensor")
                self.assertIsNone(
                    normalize_snapshot(device, metadata)["state"]["snoozeDuration"]
                )

    def test_enum_domains_and_ordinary_storage_types_still_control_validation(self):
        for read_shape, value, expected_type, expected_platform in (
            (
                {"type": "string", "kind": "enum", "values": [0, 120]},
                120,
                "number",
                "select",
            ),
            (
                {"type": "number", "kind": "enum", "values": ["off", "auto"]},
                "auto",
                "string",
                "select",
            ),
            ({"type": "bool", "kind": "flag"}, False, "bool", "switch"),
            ({"type": "number", "kind": "percent"}, 0, "number", "number"),
            ({"type": "string", "kind": "text"}, "", "string", "sensor"),
        ):
            with self.subTest(read_shape=read_shape):
                metadata, device = self._fixture(value=value)
                read = metadata["decodedProperties"]["details"][0]["reads"][0]
                read.update(read_shape)
                spec = normalize_properties(metadata)[0]
                self.assertEqual(spec["type"], expected_type)
                self.assertEqual(classify(spec), expected_platform)
                self.assertEqual(
                    normalize_snapshot(device, metadata)["state"]["snoozeDuration"],
                    value,
                )
                if read_shape["kind"] == "enum":
                    device["decodedState"]["station"]["snoozeSeconds"] = "invalid"
                    self.assertIsNone(
                        normalize_snapshot(device, metadata)["state"]["snoozeDuration"]
                    )

    def test_legacy_seconds_without_decoded_contract_preserve_passthrough(self):
        metadata, device = self._fixture()
        legacy_device = deepcopy(device)
        legacy_device.pop("decodedState")
        self.assertIs(normalize_snapshot(legacy_device, metadata), legacy_device)
        legacy_metadata = deepcopy(metadata)
        legacy_metadata.pop("decodedProperties")
        self.assertIs(normalize_snapshot(device, legacy_metadata), device)
        self.assertIs(
            normalize_properties(legacy_metadata), legacy_metadata["properties"]
        )


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
        with self.assertLogs("custom_components.eufy_sdk.api", level="WARNING"):
            self.assertEqual(await self.client.list_devices(), [self.device])
        self.assertNotIn(SN, self.client._property_cache)
        self.fail_metadata = False
        await self.client.list_devices()
        self.assertEqual(self.metadata_calls, 3)

    async def test_close_discards_metadata(self):
        await self.client.list_devices()
        await self.client.close()
        await self.client.list_devices()
        self.assertEqual(self.metadata_calls, 2)

    async def test_reset_discards_metadata_before_socket_teardown_finishes(self):
        await self.client.list_devices()
        close_started = asyncio.Event()
        finish_close = asyncio.Event()

        async def close_socket() -> None:
            close_started.set()
            await finish_close.wait()

        original = self.client.rpc

        async def rpc(cmd: str, **kwargs: Any) -> dict:
            if not self.client.connected:
                message = "not connected"
                raise EufySdkApiClientCommunicationError(message)
            return await original(cmd, **kwargs)

        socket = Mock(closed=False, close=AsyncMock(side_effect=close_socket))
        self.client._ws = socket
        self.client.rpc = AsyncMock(side_effect=rpc)
        reset = asyncio.create_task(self.client.reset_connection())
        try:
            await close_started.wait()
            self.assertFalse(self.client.connected)
            self.assertFalse(self.client._closing)
            with self.assertRaises(EufySdkApiClientCommunicationError):
                await self.client.get_properties(SN)
            self.assertNotIn(SN, self.client._property_cache)
        finally:
            finish_close.set()
            await reset
            self.client.rpc = original
        self.assertIsNone(self.client._ws)
        await self.client.list_devices()
        self.assertEqual(self.metadata_calls, 2)

    async def test_reset_rejects_inflight_metadata_before_socket_teardown_finishes(
        self,
    ):
        metadata_started = asyncio.Event()
        finish_metadata = asyncio.Event()
        close_started = asyncio.Event()
        finish_close = asyncio.Event()

        async def metadata_rpc(_cmd: str, **_kwargs: Any) -> dict:
            metadata_started.set()
            await finish_metadata.wait()
            return deepcopy(METADATA)

        async def close_socket() -> None:
            close_started.set()
            await finish_close.wait()

        self.client.rpc = AsyncMock(side_effect=metadata_rpc)
        self.client._ws = Mock(closed=False, close=AsyncMock(side_effect=close_socket))
        metadata = asyncio.create_task(self.client.get_properties(SN))
        await metadata_started.wait()
        reset = asyncio.create_task(self.client.reset_connection())
        try:
            await close_started.wait()
            finish_metadata.set()
            with self.assertRaises(EufySdkApiClientCommunicationError):
                await metadata
            self.assertNotIn(SN, self.client._property_cache)
        finally:
            finish_metadata.set()
            finish_close.set()
            await asyncio.gather(metadata, reset, return_exceptions=True)
        self.client.rpc = AsyncMock(return_value=deepcopy(METADATA))
        properties = await self.client.get_properties(SN)
        self.assertEqual(properties[0]["name"], "recordingQuality")
        self.client.rpc.assert_awaited_once_with("device.properties", sn=SN)

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
