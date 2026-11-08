# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009

import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.camera import Camera

from custom_components.eufy_sdk.camera import EufySdkCamera

BATTERY = "T8161P0000000001"
MAINS = "T8423P0000000001"
LEGACY_BATTERY = "T8170P0000000001"
LEGACY_MAINS = "T8425P0000000001"


def _camera(sn: str) -> EufySdkCamera:
    coordinator = Mock()
    coordinator.data = {
        BATTERY: {
            "capabilities": ["camera", "battery"],
            "state": {"battery": 80},
            "power": "battery",
            "stream": "/s",
        },
        # A mains-only model that still reports the battery capability and a level.
        MAINS: {
            "capabilities": ["camera", "battery"],
            "state": {"battery": 0},
            "power": "wired",
            "stream": "/s",
        },
        # A bridge without the `power` field.
        LEGACY_BATTERY: {
            "capabilities": ["camera", "battery"],
            "state": {"battery": 80},
            "stream": "/s",
        },
        LEGACY_MAINS: {"capabilities": ["camera", "battery"], "stream": "/s"},
    }
    return EufySdkCamera(coordinator, sn, "10.0.0.5", 3000, 8554)


class BatteryCameraStreamTests(unittest.IsolatedAsyncioTestCase):
    async def _starts_worker(self, sn: str) -> bool:
        with patch.object(Camera, "async_create_stream", AsyncMock()) as base:
            await _camera(sn).async_create_stream()
        return base.called

    async def test_a_battery_camera_never_starts_the_ha_stream_worker(self):
        # ha-eufy-sdk#83: the worker retries forever and each retry wakes the camera.
        with patch.object(Camera, "async_create_stream", AsyncMock()) as base:
            self.assertIsNone(await _camera(BATTERY).async_create_stream())
        base.assert_not_called()

    async def test_a_mains_camera_keeps_the_stream_worker(self):
        cam = _camera(MAINS)
        with patch.object(Camera, "async_create_stream", AsyncMock(return_value="s")):
            self.assertEqual(await cam.async_create_stream(), "s")

    async def test_without_the_power_field_the_capability_and_level_decide(self):
        self.assertFalse(await self._starts_worker(LEGACY_BATTERY))
        self.assertTrue(await self._starts_worker(LEGACY_MAINS))

    async def test_a_mains_camera_keeps_the_rtsp_restream(self):
        # The stream worker (HLS, camera.record) opens this source too.
        for sn in (MAINS, LEGACY_MAINS):
            self.assertEqual(
                await _camera(sn).stream_source(), f"rtsp://10.0.0.5:8554/{sn}"
            )

    async def test_a_battery_camera_reads_the_bridge_feed(self):
        # Only HA's go2rtc reads it: no ffmpeg remux, no DESCRIBE deadline.
        for sn in (BATTERY, LEGACY_BATTERY):
            self.assertEqual(
                await _camera(sn).stream_source(), "http://10.0.0.5:3000/s"
            )

    async def test_without_a_stream_path_the_feed_is_named_by_serial(self):
        cam = _camera(BATTERY)
        del cam.coordinator.data[BATTERY]["stream"]
        cam = EufySdkCamera(cam.coordinator, BATTERY, "10.0.0.5", 3000, 8554)
        self.assertEqual(
            await cam.stream_source(), f"http://10.0.0.5:3000/stream/{BATTERY}"
        )


if __name__ == "__main__":
    unittest.main()
