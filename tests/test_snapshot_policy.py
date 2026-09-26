# ruff: noqa: D100, D102, INP001, PT009

import unittest
from types import SimpleNamespace
from typing import Any, Self
from unittest.mock import AsyncMock, Mock, patch

from custom_components.eufy_sdk.camera import EufySdkCamera
from custom_components.eufy_sdk.select import (
    EufySdkSnapshotPolicySelect,
)
from custom_components.eufy_sdk.select import (
    async_setup_entry as async_setup_select_entry,
)
from custom_components.eufy_sdk.snapshot_policy import (
    SNAPSHOT_POLICY_AUTO,
    SNAPSHOT_POLICY_DEFAULT,
    SNAPSHOT_POLICY_LIVE,
    SNAPSHOT_POLICY_STORED,
    bridge_mode_for_policy,
    normalize_snapshot_policy,
    snapshot_url,
)


class SnapshotPolicyTests(unittest.IsolatedAsyncioTestCase):
    """Test the local policy-to-bridge contract."""

    def test_default_preserves_bare_snapshot(self) -> None:
        self.assertIsNone(bridge_mode_for_policy(None))
        self.assertIsNone(bridge_mode_for_policy(SNAPSHOT_POLICY_DEFAULT))
        self.assertEqual(
            snapshot_url("bridge", 3000, "CAM1", None),
            "http://bridge:3000/snapshot/CAM1",
        )

    def test_explicit_modes_map_to_bridge_query_values(self) -> None:
        self.assertEqual(bridge_mode_for_policy(SNAPSHOT_POLICY_AUTO), "auto")
        self.assertEqual(bridge_mode_for_policy(SNAPSHOT_POLICY_STORED), "stored")
        self.assertEqual(bridge_mode_for_policy(SNAPSHOT_POLICY_LIVE), "live")
        self.assertEqual(
            snapshot_url("bridge", 3000, "CAM1", SNAPSHOT_POLICY_AUTO),
            "http://bridge:3000/snapshot/CAM1?mode=auto",
        )
        self.assertEqual(
            snapshot_url("bridge", 3000, "CAM1", SNAPSHOT_POLICY_STORED),
            "http://bridge:3000/snapshot/CAM1?mode=stored",
        )
        self.assertEqual(
            snapshot_url("bridge", 3000, "CAM1", SNAPSHOT_POLICY_LIVE),
            "http://bridge:3000/snapshot/CAM1?mode=live",
        )

    def test_unknown_restored_value_defaults_safely(self) -> None:
        self.assertEqual(normalize_snapshot_policy("removed"), SNAPSHOT_POLICY_DEFAULT)

    async def test_select_update_is_local_and_persists_in_runtime_data(self) -> None:
        coordinator = _coordinator({"camera-a": {"stream": "camera-a"}})
        entity = EufySdkSnapshotPolicySelect(coordinator, "camera-a")
        entity.async_write_ha_state = Mock()

        await entity.async_select_option(SNAPSHOT_POLICY_LIVE)

        self.assertEqual(
            coordinator.config_entry.runtime_data.snapshot_policy,
            {"camera-a": SNAPSHOT_POLICY_LIVE},
        )
        entity.async_write_ha_state.assert_called_once_with()
        coordinator.config_entry.runtime_data.client.set.assert_not_called()

    async def test_restore_lifecycle_normalizes_saved_policy_per_camera(self) -> None:
        cases = (
            (None, SNAPSHOT_POLICY_DEFAULT),
            ("default", SNAPSHOT_POLICY_DEFAULT),
            ("auto", SNAPSHOT_POLICY_AUTO),
            ("stored", SNAPSHOT_POLICY_STORED),
            ("live", SNAPSHOT_POLICY_LIVE),
            ("unknown", SNAPSHOT_POLICY_DEFAULT),
        )
        for restored, expected in cases:
            with self.subTest(restored=restored):
                coordinator = _coordinator({"camera-a": {"stream": "camera-a"}})
                entity = EufySdkSnapshotPolicySelect(coordinator, "camera-a")
                self.assertEqual(
                    coordinator.config_entry.runtime_data.snapshot_policy,
                    {},
                    "constructing the selector must not publish before restore",
                )
                entity.async_on_remove = Mock()
                entity.async_get_last_state = AsyncMock(
                    return_value=(SimpleNamespace(state=restored) if restored else None)
                )
                coordinator.async_add_listener = Mock(return_value=lambda: None)

                await entity.async_added_to_hass()

                self.assertEqual(entity.current_option, expected)
                self.assertEqual(
                    coordinator.config_entry.runtime_data.snapshot_policy["camera-a"],
                    expected,
                )

    async def test_select_setup_only_creates_policy_for_stream_devices(self) -> None:
        coordinator = _coordinator(
            {
                "camera-stream": {"stream": "camera-stream", "name": "Camera"},
                "camera-no-stream": {"name": "Sensor"},
            }
        )
        added = []

        await async_setup_select_entry(
            None,
            coordinator.config_entry,
            added.extend,
        )

        policies = [e for e in added if isinstance(e, EufySdkSnapshotPolicySelect)]
        self.assertEqual(len(policies), 1)
        self.assertEqual(policies[0].unique_id, "camera-stream_snapshot_policy")
        self.assertEqual(
            policies[0].device_info["identifiers"],
            {("eufy_sdk", "camera-stream")},
        )

    async def test_camera_image_uses_per_camera_policy_and_stream_source_is_unchanged(
        self,
    ) -> None:
        cases = (
            (None, "http://bridge:3000/snapshot/camera-a"),
            (SNAPSHOT_POLICY_DEFAULT, "http://bridge:3000/snapshot/camera-a"),
            (SNAPSHOT_POLICY_AUTO, "http://bridge:3000/snapshot/camera-a?mode=auto"),
            (
                SNAPSHOT_POLICY_STORED,
                "http://bridge:3000/snapshot/camera-a?mode=stored",
            ),
            (SNAPSHOT_POLICY_LIVE, "http://bridge:3000/snapshot/camera-a?mode=live"),
        )
        for policy, expected_url in cases:
            with self.subTest(policy=policy):
                coordinator = _coordinator({"camera-a": {"stream": "camera-a"}})
                coordinator.config_entry.runtime_data.snapshot_policy = (
                    {} if policy is None else {"camera-a": policy}
                )
                camera = _CameraWithHass(coordinator, "camera-a", "bridge", 3000)
                session = _SnapshotSession()

                with patch(
                    "custom_components.eufy_sdk.camera.async_get_clientsession",
                    return_value=session,
                ):
                    self.assertEqual(await camera.async_camera_image(), b"jpeg")

                self.assertEqual(session.urls, [expected_url])
                self.assertEqual(
                    await camera.stream_source(), "rtsp://bridge:8554/camera-a"
                )

    async def test_two_camera_selector_and_request_state_are_isolated(self) -> None:
        coordinator = _coordinator(
            {
                "camera-a": {"stream": "camera-a"},
                "camera-b": {"stream": "camera-b"},
            }
        )
        select_a = EufySdkSnapshotPolicySelect(coordinator, "camera-a")
        select_b = EufySdkSnapshotPolicySelect(coordinator, "camera-b")
        select_a.async_write_ha_state = Mock()
        select_b.async_write_ha_state = Mock()
        await select_a.async_select_option(SNAPSHOT_POLICY_STORED)
        await select_b.async_select_option(SNAPSHOT_POLICY_LIVE)
        session = _SnapshotSession()
        camera_a = _CameraWithHass(coordinator, "camera-a", "bridge", 3000)
        camera_b = _CameraWithHass(coordinator, "camera-b", "bridge", 3000)

        with patch(
            "custom_components.eufy_sdk.camera.async_get_clientsession",
            return_value=session,
        ):
            await camera_a.async_camera_image()
            await camera_b.async_camera_image()
            await select_a.async_select_option(SNAPSHOT_POLICY_DEFAULT)
            await camera_a.async_camera_image()
            await camera_b.async_camera_image()

        self.assertEqual(
            session.urls,
            [
                "http://bridge:3000/snapshot/camera-a?mode=stored",
                "http://bridge:3000/snapshot/camera-b?mode=live",
                "http://bridge:3000/snapshot/camera-a",
                "http://bridge:3000/snapshot/camera-b?mode=live",
            ],
        )
        self.assertEqual(
            coordinator.config_entry.runtime_data.snapshot_policy,
            {"camera-a": SNAPSHOT_POLICY_DEFAULT, "camera-b": SNAPSHOT_POLICY_LIVE},
        )


def _coordinator(devices: dict[str, dict[str, Any]]) -> SimpleNamespace:
    """Build the integration runtime objects consumed by real entity constructors."""
    runtime_data = SimpleNamespace(
        snapshot_policy={},
        properties={sn: [] for sn in devices},
        client=Mock(),
    )
    entry = SimpleNamespace(runtime_data=runtime_data)
    coordinator = SimpleNamespace(
        config_entry=entry,
        data=devices,
        solix_devices={},
    )
    runtime_data.coordinator = coordinator
    return coordinator


class _CameraWithHass(EufySdkCamera):
    """Give the real camera entity a harmless Home Assistant context for tests."""

    @property
    def hass(self) -> Any:
        return object()


class _SnapshotResponse:
    status = 200

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def read(self) -> bytes:
        return b"jpeg"


class _SnapshotSession:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, *, timeout: int) -> _SnapshotResponse:
        del timeout
        self.urls.append(url)
        return _SnapshotResponse()


if __name__ == "__main__":
    unittest.main()
