# ruff: noqa: D100, D102, INP001, PT009, SLF001

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from custom_components.eufy_sdk.api import EufySdkApiClient

from custom_components.eufy_sdk.button import EufyTakeScreenshotButton
from custom_components.eufy_sdk.button import async_setup_entry as button_setup
from custom_components.eufy_sdk.const import EVENT_TYPE
from custom_components.eufy_sdk.image import EufySdkLastScreenImage
from custom_components.eufy_sdk.image import async_setup_entry as image_setup
from custom_components.eufy_sdk.pushmap import (
    EVENT_IMAGE_REFRESH,
    LAST_SCREEN_REFRESH,
)
from custom_components.eufy_sdk.select import EufySdkSnapshotPolicySelect
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


NEW = {"stream": "/stream/CAM1", "lastScreen": "/last-screen/CAM1"}
OLD = {"stream": "/stream/CAM1"}


class LastScreenGateTests(unittest.IsolatedAsyncioTestCase):
    """Nothing last-screen is offered on a bridge that does not advertise it."""

    async def _entities(self, setup: Any, device: dict[str, Any]) -> list[Any]:
        coordinator = _coordinator({"CAM1": device})
        entry = SimpleNamespace(
            runtime_data=coordinator.config_entry.runtime_data,
            data={"host": "bridge", "port": 3000},
        )
        added: list[Any] = []
        await setup(Mock(), entry, added.extend)
        return added

    async def test_button_needs_the_bridge_capability(self) -> None:
        new = await self._entities(button_setup, NEW)
        old = await self._entities(button_setup, OLD)
        self.assertTrue(any(isinstance(e, EufyTakeScreenshotButton) for e in new))
        self.assertFalse(any(isinstance(e, EufyTakeScreenshotButton) for e in old))

    async def test_image_needs_the_bridge_capability(self) -> None:
        new = await self._entities(image_setup, NEW)
        old = await self._entities(image_setup, OLD)
        self.assertTrue(any(isinstance(e, EufySdkLastScreenImage) for e in new))
        self.assertFalse(any(isinstance(e, EufySdkLastScreenImage) for e in old))

    def test_screen_policy_needs_the_bridge_capability(self) -> None:
        new = EufySdkSnapshotPolicySelect(_coordinator({"CAM1": NEW}), "CAM1")
        old = EufySdkSnapshotPolicySelect(_coordinator({"CAM1": OLD}), "CAM1")
        self.assertIn("screen", new.options)
        self.assertNotIn("screen", old.options)


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


class OpticalLastScreenTests(unittest.IsolatedAsyncioTestCase):
    """Advertised optics retain separate cached images and capture buttons."""

    async def test_capture_rpc_retains_legacy_primary_and_selects_secondary(
        self,
    ) -> None:
        client = EufySdkApiClient("bridge", 3000, Mock())
        with patch.object(
            client, "rpc", new_callable=AsyncMock, return_value={"bytes": 12}
        ) as rpc:
            self.assertEqual(await client.take_screenshot("CAM1"), 12)
            rpc.assert_awaited_once_with("screen.take", timeout=45, sn="CAM1")
            rpc.reset_mock()
            self.assertEqual(await client.take_screenshot("CAM1", view=1), 12)
            rpc.assert_awaited_once_with("screen.take", timeout=45, sn="CAM1", view=1)

    async def test_platforms_create_two_optics_and_keep_primary_identity(self) -> None:
        record = {
            "sn": "CAM1",
            "name": "Synthetic garden PoE Cam S4",
            "model": "T8E00",
            "modelName": "PoE Cam S4",
            "category": "eufy_security",
            "deviceClass": "other",
            "capabilities": ["camera", "video", "ptz"],
            "power": "wired",
            "stream": "/stream/CAM1",
            "lastScreen": "/last-screen/CAM1",
            "views": [
                {"view": 0, "id": "CAM1", "lastScreen": "/last-screen/CAM1"},
                {
                    "view": 1,
                    "id": "CAM1_view_1",
                    "lastScreen": "/last-screen/CAM1?view=1",
                },
            ],
        }
        coordinator = _coordinator({"CAM1": record})
        coordinator.config_entry.runtime_data.client.take_screenshot = AsyncMock(
            return_value=12
        )
        entry = SimpleNamespace(
            runtime_data=coordinator.config_entry.runtime_data,
            data={"host": "bridge", "port": 3000},
        )
        images = []
        buttons = []
        await image_setup(Mock(), entry, images.extend)
        await button_setup(Mock(), entry, buttons.extend)
        screens = [e for e in images if isinstance(e, EufySdkLastScreenImage)]
        captures = [e for e in buttons if isinstance(e, EufyTakeScreenshotButton)]
        self.assertEqual(
            [e.unique_id for e in screens],
            ["CAM1_last_screen", "CAM1_last_screen_view_1"],
        )
        self.assertEqual(
            [e.unique_id for e in captures],
            ["CAM1_take_screenshot", "CAM1_take_screenshot_view_1"],
        )
        self.assertEqual(screens[1]._url, "http://bridge:3000/last-screen/CAM1?view=1")
        await captures[1].async_press()
        coordinator.config_entry.runtime_data.client.take_screenshot.assert_awaited_once_with(
            "CAM1", view=1
        )
        for image in screens:
            image.hass = Mock()
            image._handle_event(
                SimpleNamespace(
                    data={"deviceSn": "CAM1", "event": LAST_SCREEN_REFRESH, "view": 1}
                )
            )
        screens[0].hass.async_create_task.assert_not_called()
        screens[1].hass.async_create_task.assert_called_once()
        screens[1].hass.async_create_task.call_args.args[0].close()

    async def test_legacy_bridge_with_views_does_not_invent_secondary_screens(
        self,
    ) -> None:
        record = {**NEW, "views": [{"view": 0}, {"view": 1}]}
        entities = await LastScreenGateTests()._entities(image_setup, record)
        self.assertEqual(
            [e.unique_id for e in entities if isinstance(e, EufySdkLastScreenImage)],
            ["CAM1_last_screen"],
        )
