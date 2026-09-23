"""Button platform — device-level actions the bridge exposes (reboot, PTZ)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from . import presets
from .const import DOMAIN
from .entity import EufySdkDeviceEntity, has_capability

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry

# Every PTZ control is keyed by the bridge action it sends, and carries how it presents:
#   {action: {"key": translation key, "icon": …, "category": under Configuration}}
#
# The d-pad, in the order a d-pad reads. Each verb is a no-arg method on the SDK's `ptz`
# surface, so the action name IS the verb. The displayed name comes from the translation
# key rather than living here, so it follows the user's language.
_PTZ_STEPS: dict[str, dict[str, Any]] = {
    "up": {"key": "tilt_up", "icon": "mdi:arrow-up"},
    "down": {"key": "tilt_down", "icon": "mdi:arrow-down"},
    "left": {"key": "pan_left", "icon": "mdi:arrow-left"},
    "right": {"key": "pan_right", "icon": "mdi:arrow-right"},
}


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create Reboot, Refresh-Last-Event and the PTZ controls (pan-tilt cameras)."""
    coordinator = entry.runtime_data.coordinator
    entities: list[ButtonEntity] = [
        EufySdkRebootButton(coordinator, sn)
        for sn, dev in coordinator.data.items()
        if dev.get("canReboot")
    ]
    # A "Refresh Last Event" button per camera/doorbell (same set as the event Image
    # entity, gated on `stream`): forces the bridge to pull the newest event cover now —
    # a manual override for when the auto-refresh raced the HomeBase writing the crop.
    entities.extend(
        EufyRefreshEventButton(coordinator, sn)
        for sn, dev in coordinator.data.items()
        if dev.get("stream")
    )
    # The d-pad and the preset actions, gated on the `ptz` capability the bridge
    # reports: a fixed camera has no `ptz` surface, so the bridge would refuse every
    # verb and the buttons would sit there as dead controls.
    for sn, dev in coordinator.data.items():
        if not has_capability(dev, "ptz"):
            continue
        entities.extend(
            EufySdkPtzButton(coordinator, sn, action, meta)
            for action, meta in _PTZ_STEPS.items()
        )
        entities.append(EufySdkGotoPresetButton(coordinator, sn))
    async_add_entities(entities)


class EufySdkRebootButton(EufySdkDeviceEntity, ButtonEntity):
    """Reboot a HomeBase — a device-level action, not a writable property."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "reboot"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a HomeBase serial."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_reboot"

    async def async_press(self) -> None:
        """Reboot the HomeBase (it drops offline for a minute or two)."""
        client = self.client
        await client.reboot(self._sn)


class EufyRefreshEventButton(EufySdkDeviceEntity, ButtonEntity):
    """Force a 'Last event' image refresh — pull the newest event cover now."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:image-refresh"
    _attr_translation_key = "refresh_last_event"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a camera/doorbell serial."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_refresh_last_event"

    async def async_press(self) -> None:
        """Ask the bridge to re-pull the newest event cover (nudges the Image)."""
        client = self.client
        await client.refresh_event_image(self._sn)


class EufySdkPtzButton(EufySdkDeviceEntity, ButtonEntity):
    """
    One pan-tilt step, in the direction this button carries.

    Fire-and-forget: P2P carries no acknowledgement, so a press that returns without
    raising means the frame left for the camera, not that the camera finished moving.
    Where it ended up arrives separately as a `ptzNotify` event, which the Image entity
    already watches.
    """

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        action: str,
        meta: dict[str, Any],
    ) -> None:
        """Bind to a camera serial and the bridge action this button sends."""
        super().__init__(coordinator, sn)
        self._action = action
        self._attr_unique_id = f"{sn}_ptz_{action.replace('.', '_')}"
        self._attr_translation_key = meta["key"]
        self._attr_icon = meta["icon"]
        self._attr_entity_category = meta.get("category")

    @property
    def _args(self) -> tuple:
        """Positional arguments for the action — a movement step takes none."""
        return ()

    async def async_press(self) -> None:
        """Send this button's PTZ action to the bridge."""
        client = self.client
        await client.action(self._sn, self._action, *self._args)


class _EufySdkPresetButton(EufySdkPtzButton):
    """Base for the two preset actions: both act on the slot the select holds."""

    _action: str
    _slug: str

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a pan-tilt camera serial."""
        super().__init__(
            coordinator,
            sn,
            self._action,
            {"key": self._attr_translation_key, "icon": self._attr_icon},
        )
        # The base derives the id from the action; these two are named for what they
        # DO, so a later change of verb (goto → preview, as already happened) does not
        # orphan the entity the user has in their dashboards.
        self._attr_unique_id = f"{sn}_{self._slug}"

    @property
    def _slot(self) -> int:
        """Return the slot the select points at, or the camera's first one."""
        entry = self.coordinator.config_entry
        slots = presets.slots_for(entry, self._sn)
        default = slots[0].index if slots else 0
        return entry.runtime_data.selected_preset.get(self._sn, default)

    @property
    def _args(self) -> tuple[int]:
        """Pass the selected slot as the action's only argument."""
        return (self._slot,)


class EufySdkGotoPresetButton(_EufySdkPresetButton):
    """
    Swing the camera onto the stored position the select points at.

    An empty slot is a silent no-op on the wire — the camera drops the frame and
    reports nothing — so a go-to against one looks like a dead button. Where the
    camera has told us the slot is empty, refuse it here instead and say why.
    """

    _action = presets.ACTION_GOTO
    _slug = "preset_goto"
    _attr_icon = "mdi:target"
    _attr_translation_key = "preset_goto"

    async def async_press(self) -> None:
        """Move to the selected slot, unless the camera says it holds nothing."""
        entry = self.coordinator.config_entry
        slot = self._slot
        known = {s.index: s for s in presets.slots_for(entry, self._sn)}
        if (chosen := known.get(slot)) is not None and not chosen.occupied:
            # Translated rather than spelled out here: this one surfaces in the UI,
            # unlike the log lines, so it follows the user's language.
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="preset_empty",
                translation_placeholders={"slot": str(slot)},
            )
        await super().async_press()
