"""Custom types for eufy_sdk."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.loader import Integration

    from .api import EufySdkApiClient
    from .coordinator import EufySdkDataUpdateCoordinator


type EufySdkConfigEntry = ConfigEntry[EufySdkData]


@dataclass
class EufySdkData:
    """Data for the EufySdk integration."""

    client: EufySdkApiClient
    coordinator: EufySdkDataUpdateCoordinator
    integration: Integration
    # Per-device property manifests ({sn: [spec, …]}), fetched once at setup.
    properties: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Which preset slot each pan-tilt camera's PTZ buttons act on ({sn: slot}). The
    # select entity owns it and the go-to / save buttons read it, so the two platforms
    # agree on a slot without finding each other through the entity registry.
    selected_preset: dict[str, int] = field(default_factory=dict)
    # Slots as each camera last reported them ({sn: [PresetSlot, …]}), read over P2P so
    # only present once the camera has been awake to answer. Empty means "not asked
    # yet", never "no presets" — see presets.py.
    preset_slots: dict[str, list[Any]] = field(default_factory=dict)
