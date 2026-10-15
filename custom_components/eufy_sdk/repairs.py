"""Repair flows: turn off stream preloading on a battery camera."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.camera.const import DATA_CAMERA_PREFS
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow

from .preload import PRELOAD_ISSUE_PREFIX

if TYPE_CHECKING:
    from homeassistant import data_entry_flow
    from homeassistant.core import HomeAssistant


class PreloadRepairFlow(RepairsFlow):
    """Turn off "Preload camera stream" for one battery camera."""

    def __init__(self, entity_id: str, camera: str) -> None:
        """Bind to the camera entity the issue names."""
        self._entity_id = entity_id
        self._camera = camera

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        """Go straight to the confirmation."""
        return await self.async_step_confirm(user_input)

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        """Turn preloading off once the user confirms."""
        if user_input is not None:
            await self.hass.data[DATA_CAMERA_PREFS].async_update(
                self._entity_id, preload_stream=False
            )
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm", description_placeholders={"camera": self._camera}
        )


async def async_create_fix_flow(
    hass: HomeAssistant,  # noqa: ARG001
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create the fix flow for a repair issue."""
    if issue_id.startswith(PRELOAD_ISSUE_PREFIX) and data:
        return PreloadRepairFlow(str(data["entity_id"]), str(data.get("camera", "")))
    return ConfirmRepairFlow()
