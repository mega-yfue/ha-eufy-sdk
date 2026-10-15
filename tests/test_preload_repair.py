"""A preloaded battery camera raises a fixable repair; the fix turns preload off."""

# ruff: noqa: INP001, S101

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.camera.const import DATA_CAMERA_PREFS

from custom_components.eufy_sdk import preload, repairs

BATTERY_CAM = {
    "name": "Front door",
    "capabilities": ["battery"],
    "state": {"battery": 80},
}
# A mains model can carry the capability but reports no battery level.
MAINS_CAM = {"name": "Garden", "capabilities": ["battery"], "state": {}}


class PreloadCheckTests(IsolatedAsyncioTestCase):
    """Raise or clear the repair issue from the camera's preload setting."""

    async def _check(self, record: dict, *, preload_on: bool) -> tuple[Mock, Mock]:
        settings = SimpleNamespace(preload_stream=preload_on)
        with (
            patch.object(
                preload,
                "get_dynamic_camera_stream_settings",
                AsyncMock(return_value=settings),
            ),
            patch.object(preload.ir, "async_create_issue") as create,
            patch.object(preload.ir, "async_delete_issue") as delete,
        ):
            await preload.async_check_preload(Mock(), "camera.front", "SN1", record)
        return create, delete

    async def test_a_preloaded_battery_camera_raises_a_fixable_issue(self) -> None:
        """The issue names the camera and carries the entity for the fix flow."""
        create, delete = await self._check(BATTERY_CAM, preload_on=True)
        create.assert_called_once()
        kwargs = create.call_args.kwargs
        assert create.call_args.args[2] == "preload_battery_camera_SN1"
        assert kwargs["is_fixable"] is True
        assert kwargs["data"] == {"entity_id": "camera.front", "camera": "Front door"}
        delete.assert_not_called()

    async def test_preload_off_clears_the_issue(self) -> None:
        """Turning preload off (e.g. by hand) removes a stale issue on next start."""
        create, delete = await self._check(BATTERY_CAM, preload_on=False)
        create.assert_not_called()
        delete.assert_called_once()

    async def test_a_mains_camera_is_never_flagged(self) -> None:
        """Preloading a mains camera costs no battery, so there is no issue."""
        create, _ = await self._check(MAINS_CAM, preload_on=True)
        create.assert_not_called()


class PreloadFixFlowTests(IsolatedAsyncioTestCase):
    """The fix flow turns preload off for the camera the issue names."""

    async def test_confirming_turns_preload_off(self) -> None:
        """Submitting the confirm step updates the camera's preferences."""
        flow = await repairs.async_create_fix_flow(
            Mock(),
            "preload_battery_camera_SN1",
            {"entity_id": "camera.front", "camera": "Front door"},
        )
        assert isinstance(flow, repairs.PreloadRepairFlow)
        prefs = Mock(async_update=AsyncMock())
        flow.hass = Mock(data={DATA_CAMERA_PREFS: prefs})
        flow.async_create_entry = Mock(return_value="done")
        assert await flow.async_step_confirm({}) == "done"
        prefs.async_update.assert_awaited_once_with(
            "camera.front", preload_stream=False
        )

    async def test_the_form_names_the_camera(self) -> None:
        """Before confirming, the form shows which camera it will change."""
        flow = repairs.PreloadRepairFlow("camera.front", "Front door")
        flow.async_show_form = Mock(side_effect=lambda **kw: kw)
        result = await flow.async_step_init()
        assert result["description_placeholders"] == {"camera": "Front door"}
