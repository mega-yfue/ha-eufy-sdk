# ruff: noqa: D100, D102, INP001, PT009
# Created with Codex.

import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.camera import Camera

from custom_components.eufy_sdk.camera import async_setup_entry

SN = "T8000P0000000001"
VIEWS = [
    {"view": 0, "id": SN, "stream": f"/stream/{SN}", "snapshot": f"/snapshot/{SN}"},
    {
        "view": 1,
        "id": f"{SN}_view_1",
        "stream": f"/stream/{SN}?view=1",
        "snapshot": f"/snapshot/{SN}?view=1",
    },
]


async def setup_camera(**changes: object) -> tuple[list, SimpleNamespace]:
    """Pass a sanitized bridge record through the production platform caller."""
    device = {
        "sn": SN,
        "name": "Synthetic garden PoE Cam S4",
        "model": "T8E00",
        "modelName": "PoE Cam S4",
        "category": "eufy_security",
        "deviceClass": "other",
        "stationSn": "T8000P0000000000",
        "channel": 3,
        "capabilities": ["camera", "video", "ptz"],
        "power": "wired",
        "state": {"deviceStatus": True},
        "stream": f"/stream/{SN}",
        "views": deepcopy(VIEWS),
        **changes,
    }
    # Omitting the field represents an older bridge, rather than a model heuristic.
    if device["views"] is None:
        del device["views"]
    coordinator = Mock()
    coordinator.data = {SN: device}
    coordinator.last_update_success = True
    runtime = SimpleNamespace(coordinator=coordinator, snapshot_policy={})
    entry = SimpleNamespace(
        data={"host": "bridge", "port": 3000, "go2rtc_rtsp_port": 8554},
        runtime_data=runtime,
    )
    coordinator.config_entry = entry
    added = []
    await async_setup_entry(Mock(), entry, added.extend)
    return added, coordinator


class CameraViewTests(unittest.IsolatedAsyncioTestCase):
    """Exercise entity creation and public media methods on advertised views."""

    async def test_setup_creates_two_entities_with_stable_primary_identity(
        self,
    ) -> None:
        cameras, _ = await setup_camera()
        self.assertEqual(
            [cam.unique_id for cam in cameras],
            [f"{SN}_camera", f"{SN}_camera_view_1"],
        )
        self.assertEqual(cameras[0].device_info, cameras[1].device_info)
        self.assertIsNone(cameras[0].name)
        self.assertEqual(cameras[1].name, "View 1")

    async def test_media_uses_both_advertised_sources(self) -> None:
        cameras, _ = await setup_camera()
        self.assertEqual(
            [await cam.stream_source() for cam in cameras],
            [f"rtsp://bridge:8554/{SN}", f"rtsp://bridge:8554/{SN}_view_1"],
        )

    async def test_selected_snapshot_retains_view_and_all_acquisition_policies(
        self,
    ) -> None:
        cameras, coordinator = await setup_camera()
        self.assertEqual(len(cameras), 2)
        for policy in (None, "default", "auto", "stored", "live"):
            with self.subTest(policy=policy):
                coordinator.config_entry.runtime_data.snapshot_policy = {SN: policy}
                response = AsyncMock()
                response.status = 200
                response.read.return_value = b"selected-jpeg"
                session = Mock()
                session.get.return_value.__aenter__ = AsyncMock(return_value=response)
                session.get.return_value.__aexit__ = AsyncMock(return_value=False)
                for cam in cameras:
                    cam.hass = Mock()
                with patch(
                    "custom_components.eufy_sdk.camera.async_get_clientsession",
                    return_value=session,
                ):
                    for cam in cameras:
                        self.assertEqual(
                            await cam.async_camera_image(), b"selected-jpeg"
                        )
                mode = "" if policy in (None, "default") else f"mode={policy}"
                self.assertEqual(
                    [call.args[0] for call in session.get.call_args_list],
                    [
                        f"http://bridge:3000/snapshot/{SN}"
                        + (f"?{mode}" if mode else ""),
                        f"http://bridge:3000/snapshot/{SN}?view=1"
                        + (f"&{mode}" if mode else ""),
                    ],
                )

    async def test_legacy_and_single_view_bridges_keep_one_primary_camera(self) -> None:
        for views in (None, [VIEWS[0]]):
            with self.subTest(views=views):
                cameras, _ = await setup_camera(views=views)
                self.assertEqual([cam.unique_id for cam in cameras], [f"{SN}_camera"])
                self.assertEqual(
                    await cameras[0].stream_source(), f"rtsp://bridge:8554/{SN}"
                )

    async def test_inventory_is_not_derived_from_model_or_view_order(self) -> None:
        view = {
            "view": 2,
            "id": "synthetic-secondary",
            "stream": "/stream/custom?view=2",
            "snapshot": "/snapshot/custom?view=2",
        }
        cameras, _ = await setup_camera(model="SYNTHETIC", views=[view, VIEWS[0]])
        self.assertEqual(
            [cam.unique_id for cam in cameras], [f"{SN}_camera_view_2", f"{SN}_camera"]
        )
        self.assertEqual(
            await cameras[0].stream_source(), "rtsp://bridge:8554/synthetic-secondary"
        )
        response = AsyncMock()
        response.status = 200
        response.read.return_value = b"selected-jpeg"
        session = Mock()
        session.get.return_value.__aenter__ = AsyncMock(return_value=response)
        session.get.return_value.__aexit__ = AsyncMock(return_value=False)
        cameras[0].hass = Mock()
        with patch(
            "custom_components.eufy_sdk.camera.async_get_clientsession",
            return_value=session,
        ):
            await cameras[0].async_camera_image()
        session.get.assert_called_once_with(
            "http://bridge:3000/snapshot/custom?view=2", timeout=20
        )

    async def test_empty_inventory_does_not_invent_a_view(self) -> None:
        cameras, _ = await setup_camera(views=[])
        self.assertEqual(cameras, [])

    async def test_removed_secondary_view_is_unavailable_without_hiding_primary(
        self,
    ) -> None:
        cameras, coordinator = await setup_camera()
        self.assertEqual(len(cameras), 2)
        self.assertTrue(all(cam.available for cam in cameras))
        coordinator.data[SN]["views"] = [VIEWS[0]]
        self.assertTrue(cameras[0].available)
        self.assertFalse(cameras[1].available)
        del coordinator.data[SN]["views"]
        self.assertTrue(cameras[0].available)
        self.assertFalse(cameras[1].available)

    async def test_battery_worker_protection_applies_to_each_view(self) -> None:
        cameras, _ = await setup_camera(
            power="battery", capabilities=["camera", "battery"]
        )
        self.assertEqual(len(cameras), 2)
        with patch.object(Camera, "async_create_stream", AsyncMock()) as worker:
            for cam in cameras:
                self.assertIsNone(await cam.async_create_stream())
            worker.assert_not_called()

    async def test_devices_without_live_stream_still_have_no_camera(self) -> None:
        cameras, _ = await setup_camera(stream=None)
        self.assertEqual(cameras, [])
