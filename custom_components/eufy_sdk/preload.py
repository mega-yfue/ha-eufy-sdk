"""
Warn when Home Assistant preloads a battery camera's stream.

With "Preload camera stream" on, Home Assistant keeps its stream worker running
for good and reconnects it whenever the stream ends. On a battery camera every
reconnect wakes the camera, and the bridge's battery budget ends a stream after
about 55 s, so the camera is woken again and again and drains. A repair issue
names the camera and offers to turn preloading off.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.camera.prefs import get_dynamic_camera_stream_settings
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

PRELOAD_ISSUE_PREFIX = "preload_battery_camera_"
PRELOAD_TRANSLATION_KEY = "preload_battery_camera"


def on_battery_power(record: dict[str, Any] | None) -> bool:
    """
    Whether a camera runs on battery.

    Some mains models still carry the `battery` capability, but only a camera on
    battery reports a level, so both are required.
    """
    record = record or {}
    return "battery" in (record.get("capabilities") or []) and (
        (record.get("state") or {}).get("battery") is not None
    )


async def async_check_preload(
    hass: HomeAssistant, entity_id: str, sn: str, record: dict[str, Any] | None
) -> None:
    """Raise the repair issue for a preloaded battery camera, or clear it."""
    issue_id = f"{PRELOAD_ISSUE_PREFIX}{sn}"
    settings = await get_dynamic_camera_stream_settings(hass, entity_id)
    if not (on_battery_power(record) and settings.preload_stream):
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    name = (record or {}).get("name") or sn
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=PRELOAD_TRANSLATION_KEY,
        translation_placeholders={"camera": name},
        data={"entity_id": entity_id, "camera": name},
    )
