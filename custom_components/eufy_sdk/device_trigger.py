"""
Device triggers — named "Ringing / Motion / Person / …" triggers per device.

An `event` entity doesn't surface named device triggers on its own, so a doorbell shows
no "ringing" option in the automation UI. This maps the SDK's push events onto proper
device triggers (gated by capability), so picking the device offers e.g. "Doorbell
ringing" directly — matching the older eufy integration. Each trigger fires on the
`<DOMAIN>_event` bus message for that device (the real-time push path the entities use).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, EVENT_TYPE

if TYPE_CHECKING:
    from homeassistant.core import CALLBACK_TYPE, HomeAssistant
    from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
    from homeassistant.helpers.typing import ConfigType


# device-trigger type -> (bus event name, required capability). Gated so a device only
# offers triggers for events it can actually emit.
TRIGGER_MAP: dict[str, tuple[str, str]] = {
    "ringing": ("doorbellPress", "doorbell"),
    "motion": ("motion", "motion"),
    "person": ("personDetected", "person_detection"),
    "pet": ("petDetection", "person_detection"),
    "dog": ("dogDetected", "person_detection"),
    "vehicle": ("vehicleDetected", "person_detection"),
    "stranger": ("strangerDetected", "person_detection"),
    "sound": ("soundDetected", "motion"),
    "crying": ("cryingDetected", "motion"),
    "package_delivered": ("packageDelivered", "doorbell"),
    "package_taken": ("packageTaken", "doorbell"),
    "package_stranded": ("packageStranded", "doorbell"),
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In(set(TRIGGER_MAP))}
)


def _serial_for_device(hass: HomeAssistant, device_id: str) -> str | None:
    """Resolve a HA device_id back to the eufy serial (its (DOMAIN, sn) identifier)."""
    device = dr.async_get(hass).async_get(device_id)
    if not device:
        return None
    return next(
        (ident for domain, ident in device.identifiers if domain == DOMAIN), None
    )


def _capabilities(hass: HomeAssistant, serial: str) -> set[str]:
    """Return the device's capability set, from whichever config entry holds it."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        data = getattr(entry, "runtime_data", None)
        record = data.coordinator.data.get(serial) if data else None
        if record:
            return set(record.get("capabilities", []))
    return set()


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """Offer the named triggers this device's capabilities support."""
    serial = _serial_for_device(hass, device_id)
    if not serial:
        return []
    caps = _capabilities(hass, serial)
    base = {CONF_PLATFORM: "device", CONF_DOMAIN: DOMAIN, CONF_DEVICE_ID: device_id}
    return [
        {**base, CONF_TYPE: t} for t, (_, cap) in TRIGGER_MAP.items() if cap in caps
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Fire when this device's mapped push event arrives on the bus."""
    serial = _serial_for_device(hass, config[CONF_DEVICE_ID])
    bus_event = TRIGGER_MAP[config[CONF_TYPE]][0]
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_TYPE,
            event_trigger.CONF_EVENT_DATA: {"event": bus_event, "deviceSn": serial},
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
