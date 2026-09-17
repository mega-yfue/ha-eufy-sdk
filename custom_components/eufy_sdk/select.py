"""Select platform — one select per writable enum property, plus the Preset."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.restore_state import RestoreEntity

from . import presets
from .const import ATTR_SLOTS, SLOT_REREAD_SECS
from .entity import (
    EufySdkDeviceEntity,
    EufySdkPropertyEntity,
    classify,
    has_capability,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a select per writable enum property, plus the Presets."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SelectEntity] = [
        EufySdkSelect(coordinator, sn, spec)
        for sn in coordinator.data
        for spec in entry.runtime_data.properties.get(sn, [])
        if classify(spec) == "select"
    ]
    entities.extend(
        EufySdkPresetSelect(coordinator, sn)
        for sn, dev in coordinator.data.items()
        if has_capability(dev, "ptz")
    )
    async_add_entities(entities)


class EufySdkSelect(EufySdkPropertyEntity, SelectEntity):
    """A writable enum property as a select — options are the enum labels."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        spec: dict[str, Any],
    ) -> None:
        """Build the raw<->label maps from the spec's enumValues."""
        super().__init__(coordinator, sn, spec)
        # enumValues is {raw: label}; JSON object keys arrive as strings.
        self._label_by_raw = {str(k): str(v) for k, v in spec["enumValues"].items()}
        self._raw_by_label = {v: k for k, v in self._label_by_raw.items()}
        self._attr_options = list(self._label_by_raw.values())

    @property
    def current_option(self) -> str | None:
        """The label for the property's current raw value."""
        v = self.prop_value
        return None if v is None else self._label_by_raw.get(str(v))

    async def async_select_option(self, option: str) -> None:
        """Write the raw value behind the chosen label."""
        raw = self._raw_by_label.get(option)
        if raw is None:
            return
        # Send an int when the raw code is numeric, else the raw string.
        value: int | str = int(raw) if raw.lstrip("-").isdigit() else raw
        await self.write(value)


class EufySdkPresetSelect(EufySdkDeviceEntity, SelectEntity, RestoreEntity):
    """
    Which stored preset slot this camera's go-to / save buttons act on.

    Which slot is chosen is not a device reading: nothing on the wire reports where
    a camera is parked or which preset it last used, so the CHOICE is local and gets
    restored across restarts. The slots themselves are the camera's, though, and are
    read from it — the count and the numbering differ by model, and only the camera
    knows which ones hold a position.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:map-marker-multiple"
    _attr_translation_key = "preset"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Start from the fallback slots; the real ones arrive on the first read."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_preset_slot"
        self._last_read = 0.0
        self._apply(presets.slots_for(coordinator.config_entry, sn))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Carry the slots in the state, so the next start can restore them."""
        return {ATTR_SLOTS: presets.as_dicts(self._slots)}

    async def async_added_to_hass(self) -> None:
        """Restore last run's slots and choice, then ask the camera for the truth."""
        await super().async_added_to_hass()
        entry = self.coordinator.config_entry
        last = await self.async_get_last_state()
        if last is not None:
            # Last run's slots beat the fallback numbering: they came from this very
            # camera, so a restart with the camera asleep still offers the real ones.
            if restored := presets.from_dicts(last.attributes.get(ATTR_SLOTS)):
                presets.remember(entry, self._sn, restored)
                self._apply(restored)
            # The stored state is a LABEL, and labels move — an empty slot is named
            # differently once saved. Match on the slot number inside it.
            self._select_slot(_slot_in(last.state))
        self._publish()
        # Best-effort: the camera may be asleep, and a battery camera must not be
        # woken just to refresh a list. Whatever we already have stands until then.
        await self._reread()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-read the slots whenever the camera happens to be awake already."""
        # Presets can be created in the eufy app, and nothing tells us when. Rather
        # than poll — the read is P2P and would wake a battery camera — piggyback on
        # a camera that something else is already streaming, no more often than
        # SLOT_REREAD_SECS. A camera that never streams simply keeps its last list.
        if self.device.get("streaming") and self._read_is_due():
            self.hass.async_create_task(self._reread())
        super()._handle_coordinator_update()

    def _read_is_due(self) -> bool:
        """Return whether enough time has passed to ask the camera again."""
        return time.monotonic() - self._last_read >= SLOT_REREAD_SECS

    async def _reread(self) -> None:
        """Ask the camera for its slots, keeping the old ones if it cannot answer."""
        self._last_read = time.monotonic()
        entry = self.coordinator.config_entry
        self._apply(await presets.async_refresh_slots(entry, self._sn))
        self._publish()
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Point this camera's preset buttons at another slot."""
        self._attr_current_option = option
        self._publish()
        self.async_write_ha_state()

    def _apply(self, slots: list[presets.PresetSlot]) -> None:
        """Rebuild the options from the slots, keeping the chosen one if it survives."""
        self._slots = slots
        chosen = _slot_in(self._attr_current_option)
        self._attr_options = [s.label for s in slots]
        self._select_slot(chosen)

    def _select_slot(self, slot: int | None) -> None:
        """Select the option for a slot number, falling back to the first one."""
        match = next((s for s in self._slots if s.index == slot), None)
        self._attr_current_option = (
            match.label if match else (self._slots[0].label if self._slots else None)
        )

    def _publish(self) -> None:
        """Share the slot with the button platform through the entry's runtime data."""
        slot = _slot_in(self._attr_current_option)
        if slot is not None:
            self.coordinator.config_entry.runtime_data.selected_preset[self._sn] = slot


def _slot_in(label: str | None) -> int | None:
    """Read the slot number out of a label ('0 · HOME' -> 0)."""
    if not label:
        return None
    head = label.split(" ", 1)[0]
    return int(head) if head.isdigit() else None
