# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009

import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.camera import Camera

from custom_components.eufy_sdk.camera import EufySdkCamera

BATTERY = "T8161P0000000001"
MAINS = "T8425P0000000001"


def _camera(sn: str) -> EufySdkCamera:
    coordinator = Mock()
    coordinator.data = {
        BATTERY: {"capabilities": ["camera", "video", "battery"], "stream": "/s"},
        MAINS: {"capabilities": ["camera", "video"], "stream": "/s"},
    }
    return EufySdkCamera(coordinator, sn, "10.0.0.5", 3000, 8554)


class BatteryCameraStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_battery_camera_never_starts_the_ha_stream_worker(self):
        # ha-eufy-sdk#83: the worker retries forever and each retry wakes the camera.
        with patch.object(Camera, "async_create_stream", AsyncMock()) as base:
            self.assertIsNone(await _camera(BATTERY).async_create_stream())
        base.assert_not_called()

    async def test_a_battery_camera_is_pulled_straight_from_the_bridge(self):
        self.assertEqual(
            await _camera(BATTERY).stream_source(),
            f"ffmpeg:http://10.0.0.5:3000/stream/{BATTERY}#video=copy#async",
        )

    async def test_a_mains_camera_keeps_the_rtsp_restream_and_stream_worker(self):
        cam = _camera(MAINS)
        self.assertEqual(await cam.stream_source(), f"rtsp://10.0.0.5:8554/{MAINS}")
        with patch.object(Camera, "async_create_stream", AsyncMock(return_value="s")):
            self.assertEqual(await cam.async_create_stream(), "s")


if __name__ == "__main__":
    unittest.main()
