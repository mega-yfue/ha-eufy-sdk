"""
Light platform — eufy `smart_light` devices (on/off, brightness, RGB colour).

A Permanent-Outdoor-Lights run is really one light, so present it as a HA `light`
with a colour wheel, not a bare switch + number. On/off + brightness go over
`device.set` (writable properties); colour goes over the new `device.action` -> the
SDK's `setColor`, wire-confirmed on this family.

The device reports no authoritative RGB back (a colour write acknowledges publication
only), so the shown colour is held optimistically. On/off + brightness DO report back,
but only on the light's realtime push (a cloud poll won't carry them), so after a write
we hold the intended value and schedule one delayed reconcile, like the property path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from .const import LOGGER
from .entity import POST_WRITE_REFRESH_SECS, EufySdkDeviceEntity, has_capability

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry

# smart_light state keys owned by this entity (see the SDK `smart_light` capability).
POWER = "lightPower"
BRIGHTNESS = "lightBrightness"  # 0..100 on the wire; HA brightness is 0..255
RUNNING_EFFECT = "lightCloudEffectId"  # the effect actually playing (0 = none)
# Props this platform owns, so the generic switch/number platforms skip them.
LIGHT_OWNED_PROPS = frozenset({POWER, BRIGHTNESS})
# Read-only effect internals now shown by name (effect picker + "Light Effect" sensor) —
# hidden from the generic sensor platform so smart_light gets no bare-number sensors.
LIGHT_HIDDEN_PROPS = frozenset(
    {"lightEffectId", "lightCloudEffectId", "lightEffectMode"}
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one light per device that has the `smart_light` capability."""
    coordinator = entry.runtime_data.coordinator
    smart_lights = [
        sn for sn, dev in coordinator.data.items() if has_capability(dev, "smart_light")
    ]
    if not smart_lights:
        return
    # Fetch the effect gallery once (account-wide, bridge-cached), shared across lights.
    # Best-effort: an old bridge without `light.effects` or a fetch error = no effects.
    effects: list[dict] = []
    try:
        effects = await entry.runtime_data.client.list_effects()
    except Exception:  # noqa: BLE001 - effects are optional; on/off/brightness/colour still work
        LOGGER.debug(
            "light effect catalogue unavailable; lights get no effect list",
            exc_info=True,
        )
    async_add_entities(
        EufySdkSmartLight(coordinator, sn, effects) for sn in smart_lights
    )


class EufySdkSmartLight(EufySdkDeviceEntity, LightEntity):
    """A eufy smart-lighting run as a single HA light (on/off + brightness + RGB)."""

    _attr_name = None  # the light IS the device
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        effects: list[dict],
    ) -> None:
        """Bind to a smart-light serial; start colour at white until one is set."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_light"
        self._rgb: tuple[int, int, int] = (255, 255, 255)
        # Effect gallery (shared, fetched once). Build both directions in one pass.
        self._effect_by_name: dict[str, int] = {}
        self._name_by_id: dict[int, str] = {}
        for e in effects:
            name = e.get("name")
            if name:
                self._effect_by_name[name] = e["id"]
                self._name_by_id[e["id"]] = name
        if self._effect_by_name:
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = sorted(self._effect_by_name)
        self._assumed_effect: str | None = None
        # Optimistic holds until the delayed reconcile — the wire is push, not polled.
        self._assumed_on: bool | None = None
        self._assumed_pct: int | None = None
        self._refresh_unsub = None

    @property
    def _state(self) -> dict:
        return self.device.get("state", {})

    @property
    def is_on(self) -> bool | None:
        """On/off — the optimistic value while a write settles, else reported state."""
        if self._assumed_on is not None:
            return self._assumed_on
        v = self._state.get(POWER)
        return None if v is None else bool(v)

    @property
    def brightness(self) -> int | None:
        """Brightness 0..255, from the device's 0..100 (optimistic while settling)."""
        pct = (
            self._assumed_pct
            if self._assumed_pct is not None
            else self._state.get(BRIGHTNESS)
        )
        if not isinstance(pct, (int, float)):
            return None
        return round(max(0, min(100, pct)) * 255 / 100)

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        """The last colour we set — the device reports none back."""
        return self._rgb

    @property
    def effect(self) -> str | None:
        """The running effect's name (None when a plain colour is set, or off)."""
        if self._assumed_effect is not None:
            return self._assumed_effect or None
        rid = self._state.get(RUNNING_EFFECT)
        if isinstance(rid, (int, float)) and int(rid) in self._name_by_id:
            return self._name_by_id[int(rid)]
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Apply effect/colour/brightness if given, then ensure power on."""
        client = self.client
        if ATTR_EFFECT in kwargs and kwargs[ATTR_EFFECT] in self._effect_by_name:
            name = kwargs[ATTR_EFFECT]
            self._assumed_effect = name
            await client.action(self._sn, "setEffect", self._effect_by_name[name])
        if ATTR_RGB_COLOR in kwargs:
            r, g, b = (int(c) for c in kwargs[ATTR_RGB_COLOR])
            self._rgb = (r, g, b)
            self._assumed_effect = ""  # a plain colour stops any running effect
            await client.action(self._sn, "setColor", {"red": r, "green": g, "blue": b})
        if ATTR_BRIGHTNESS in kwargs:
            pct = round(kwargs[ATTR_BRIGHTNESS] * 100 / 255)
            self._assumed_pct = pct
            await client.set_property(self._sn, BRIGHTNESS, pct)
        self._assumed_on = True
        await client.set_property(self._sn, POWER, value=True)
        self.async_write_ha_state()
        self._schedule_reconcile()

    async def async_turn_off(self, **_: Any) -> None:
        """Turn the run off."""
        client = self.client
        self._assumed_on = False
        await client.set_property(self._sn, POWER, value=False)
        self.async_write_ha_state()
        self._schedule_reconcile()

    def _schedule_reconcile(self) -> None:
        """One delayed pull to reconcile the optimistic hold with reported state."""
        if self._refresh_unsub is not None:
            self._refresh_unsub()
        self._refresh_unsub = async_call_later(
            self.hass, POST_WRITE_REFRESH_SECS, self._reconcile
        )

    @callback
    def _reconcile(self, _now: Any) -> None:
        """Drop the optimistic holds and re-render to the reported state."""
        self._refresh_unsub = None
        self._assumed_on = None
        self._assumed_pct = None
        self._assumed_effect = None
        self.hass.async_create_task(self.coordinator.async_request_refresh())
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any pending reconcile timer."""
        if self._refresh_unsub is not None:
            self._refresh_unsub()
            self._refresh_unsub = None
