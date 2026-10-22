# ruff: noqa: D100, D102, INP001, PT009, SLF001

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from custom_components.eufy_sdk.button import EufyTakeScreenshotButton
from custom_components.eufy_sdk.const import EVENT_TYPE
from custom_components.eufy_sdk.image import EufySdkLastScreenImage
from custom_components.eufy_sdk.pushmap import (
    EVENT_IMAGE_REFRESH,
    LAST_SCREEN_REFRESH,
)
from custom_components.eufy_sdk.snapshot_policy import (
    SNAPSHOT_POLICY_SCREEN,
    bridge_mode_for_policy,
    snapshot_url,
)


class LastScreenTests(unittest.IsolatedAsyncioTestCase):
    """The last screen changes only on live viewing or the screenshot button."""

    async def test_screen_policy_asks_the_bridge_for_the_last_screen(self) -> None:
        self.assertEqual(bridge_mode_for_policy(SNAPSHOT_POLICY_SCREEN), "screen")
        self.assertEqual(
            snapshot_url("bridge", 3000, "CAM1", SNAPSHOT_POLICY_SCREEN),
            "http://bridge:3000/snapshot/CAM1?mode=screen",
        )

    async def test_button_takes_a_screenshot_through_the_bridge(self) -> None:
        coordinator = _coordinator({"CAM1": {"stream": "CAM1"}})
        coordinator.config_entry.runtime_data.client.take_screenshot = AsyncMock(
            return_value=1234
        )
        button = EufyTakeScreenshotButton(coordinator, "CAM1")

        await button.async_press()

        self.assertEqual(button.unique_id, "CAM1_take_screenshot")
        coordinator.config_entry.runtime_data.client.take_screenshot.assert_awaited_once_with(
            "CAM1"
        )

    async def test_image_ignores_the_coordinator_poll(self) -> None:
        image, hass = _image()
        with patch(
            "custom_components.eufy_sdk.entity.CoordinatorEntity._handle_coordinator_update"
        ):
            image._handle_coordinator_update()
        hass.async_create_task.assert_not_called()

    async def test_image_refreshes_only_on_its_own_nudge(self) -> None:
        image, hass = _image()
        for event in (EVENT_IMAGE_REFRESH, "motionDetected"):
            image._handle_event(_event("CAM1", event))
        image._handle_event(_event("CAM2", LAST_SCREEN_REFRESH))
        hass.async_create_task.assert_not_called()

        image._handle_event(_event("CAM1", LAST_SCREEN_REFRESH))
        hass.async_create_task.assert_called_once()
        hass.async_create_task.call_args.args[0].close()


def _event(sn: str, event: str) -> SimpleNamespace:
    return SimpleNamespace(event_type=EVENT_TYPE, data={"deviceSn": sn, "event": event})


def _image() -> tuple[EufySdkLastScreenImage, Mock]:
    hass = Mock()
    image = EufySdkLastScreenImage(
        hass, _coordinator({"CAM1": {"stream": "CAM1"}}), "CAM1", "bridge", 3000
    )
    image.hass = hass
    return image, hass


def _coordinator(devices: dict[str, dict[str, Any]]) -> SimpleNamespace:
    runtime_data = SimpleNamespace(
        snapshot_policy={},
        properties={sn: [] for sn in devices},
        client=Mock(),
    )
    coordinator = SimpleNamespace(
        config_entry=SimpleNamespace(runtime_data=runtime_data),
        data=devices,
        solix_devices={},
    )
    runtime_data.coordinator = coordinator
    return coordinator
