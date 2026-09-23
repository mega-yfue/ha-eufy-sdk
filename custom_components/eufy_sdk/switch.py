"""Switch platform — writable booleans, plus per-bit switches for known bitfields."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from .bespoke import BITFIELD_SWITCHES
from .entity import (
    EufySdkPropertyEntity,
    EufySolixEntity,
    classify,
    has_capability,
    is_setting,
    solix_devices_with,
)
from .light import LIGHT_OWNED_PROPS
from .lock import LOCK_OWNED_PROPS

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
    """Create a switch per writable bool, and per-bit switches for known bitfields."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SwitchEntity] = []
    for sn in coordinator.data:
        is_smart_light = has_capability(coordinator.data[sn], "smart_light")
        is_lock = has_capability(coordinator.data[sn], "lock")
        for spec in entry.runtime_data.properties.get(sn, []):
            # The light platform owns lightPower/lightBrightness for smart_light — don't
            # also surface them as a bare switch/number (would double the control).
            if is_smart_light and spec["name"] in LIGHT_OWNED_PROPS:
                continue
            # The lock platform owns the 'locked' — don't also surface it as a switch.
            if is_lock and spec["name"] in LOCK_OWNED_PROPS:
                continue
            kind = classify(spec)
            if kind == "switch":
                entities.append(EufySdkSwitch(coordinator, sn, spec))
            elif kind == "bitfield" and spec["name"] in BITFIELD_SWITCHES:
                bf = BITFIELD_SWITCHES[spec["name"]]
                entities.extend(
                    EufyBitmaskSwitch(
                        coordinator,
                        sn,
                        spec,
                        {"key": key, "bit": bit, "base": bf["base"]},
                    )
                    for key, bit in bf["bits"].items()
                )

    # Anker Solix (separate account): a Solarbank's ambient light — a standalone switch
    # that reflects the device state (ambientLightOn, decoded from the ff09 telemetry).
    entities.extend(
        EufySolixLightSwitch(coordinator, sn)
        for sn, _ in solix_devices_with(coordinator, "battery")
    )

    async_add_entities(entities)


class EufySolixLightSwitch(EufySolixEntity, SwitchEntity):
    """
    A Solarbank's ambient light as a switch — reflects the DEVICE state.

    Toggling issues an encrypted `set_device_attrs` write through the bridge. The state
    is read back from the `ff09` telemetry: the SDK decodes `ambientLightOn` (1/0) from
    tag `0xba` bit 0x20 (inverted), live-confirmed on an AE103 across app + HA toggles.
    So a change made outside HA (the app / physical button) is reflected within a few
    seconds via `solixReading` events, and this is NOT assumed-state. The value seeds
    from the bridge's `solix.devices` snapshot and updates on live events. To avoid a UI
    flicker while the ~seconds-late telemetry catches up, a toggle we issue sets the
    shown state optimistically; the next reading confirms it. Device is `solix:<sn>`.
    """

    _attr_name = "Ambient Light"
    _attr_icon = "mdi:led-strip-variant"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a Solix Solarbank's ambient light."""
        super().__init__(coordinator, sn, "ambient_light", watch=("ambientLightOn",))

    @property
    def is_on(self) -> bool | None:
        """The light's state from telemetry (None until a reading arrives)."""
        v = self.solix_value("ambientLightOn")
        return None if v is None else bool(v)

    async def async_turn_on(self, **_: Any) -> None:
        """Turn the ambient light on (optimistic; telemetry confirms shortly)."""
        await self.client.set_solix_light(self._sn, on=True)
        self._solix_values["ambientLightOn"] = True
        self.async_write_ha_state()

    async def async_turn_off(self, **_: Any) -> None:
        """Turn the ambient light off (optimistic; telemetry confirms shortly)."""
        await self.client.set_solix_light(self._sn, on=False)
        self._solix_values["ambientLightOn"] = False
        self.async_write_ha_state()


class EufySdkSwitch(EufySdkPropertyEntity, SwitchEntity):
    """A writable boolean property as a switch."""

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        spec: dict[str, Any],
    ) -> None:
        """Put a setting toggle under Configuration; leave a primary control up top."""
        super().__init__(coordinator, sn, spec)
        if is_setting(self._prop):
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        """On when the property's live value is truthy."""
        v = self.prop_value
        return None if v is None else bool(v)

    async def async_turn_on(self, **_: Any) -> None:
        """Set the property true."""
        await self.write(value=True)

    async def async_turn_off(self, **_: Any) -> None:
        """Set the property false."""
        await self.write(value=False)


class EufyBitmaskSwitch(EufySdkPropertyEntity, SwitchEntity):
    """One bit of a bitfield property as a switch (writes back the whole mask)."""

    # Each bit is its own control ("Detect human"), not the parent property, so the
    # generic property label must not be applied over the per-bit name.
    _named_by_translation = True

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        spec: dict[str, Any],
        bitdef: dict[str, Any],
    ) -> None:
        """Bind to a single bit of the parent bitfield property ({key, bit, base})."""
        super().__init__(coordinator, sn, spec)
        self._bit: int = bitdef["bit"]
        self._base: int = bitdef["base"]
        self._attr_unique_id = f"{sn}_{self._prop}_{self._bit}"
        self._attr_translation_key = bitdef["key"]
        self._attr_entity_category = EntityCategory.CONFIG

    def _mask(self) -> int:
        """Return the current full bitmask value (falls back to the enable base)."""
        v = self.prop_value
        return int(v) if isinstance(v, (int, float)) else self._base

    @property
    def is_on(self) -> bool | None:
        """On when this bit is set in the current mask."""
        v = self.prop_value
        return None if v is None else bool(int(v) & self._bit)

    async def async_turn_on(self, **_: Any) -> None:
        """Set this bit (keeping the enable base and the other bits)."""
        await self.write(self._mask() | self._bit | self._base)

    async def async_turn_off(self, **_: Any) -> None:
        """Clear this bit (keeping the enable base and the other bits)."""
        await self.write((self._mask() & ~self._bit) | self._base)
