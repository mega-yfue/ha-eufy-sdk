"""Number platform — one number per writable numeric property."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, NamedTuple

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_SOC_REFRESH,
    DEFAULT_SOC_REFRESH_SEC,
)
from .entity import (
    EufySdkPropertyEntity,
    EufySolixEntity,
    classify,
    has_capability,
    solix_devices_with,
)
from .light import LIGHT_OWNED_PROPS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry


# The manifest carries no min/max, so pick a sane range from the value's `kind`.
_RANGE_BY_KIND = {"percent": (0, 100), "seconds": (0, 86400), "degrees": (0, 360)}
_DEFAULT_RANGE = (0, 65535)
# Kinds with a small, bounded range read better as a slider than a text box.
_SLIDER_KINDS = {"percent", "degrees"}


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create numbers for writable properties, plus Solix SOC-limit sliders."""
    coordinator = entry.runtime_data.coordinator
    entities: list[NumberEntity] = [
        EufySdkNumber(coordinator, sn, spec)
        for sn in coordinator.data
        for spec in entry.runtime_data.properties.get(sn, [])
        if classify(spec) == "number"
        # The light platform owns lightBrightness for smart_light (see switch.py).
        and not (
            spec["name"] in LIGHT_OWNED_PROPS
            and has_capability(coordinator.data[sn], "smart_light")
        )
    ]

    # Anker Solix (separate account): a Solarbank's discharge/charge limits as sliders.
    # These write the cloud SOC block (param_type 27) and reflect live `b5` telemetry.
    for sn, _ in solix_devices_with(coordinator, "battery"):
        entities.append(EufySolixSocLimitNumber(coordinator, sn, SOC_DISCHARGE))
        entities.append(EufySolixSocLimitNumber(coordinator, sn, SOC_CHARGE))

    async_add_entities(entities)


class EufySdkNumber(EufySdkPropertyEntity, NumberEntity):
    """A writable numeric property as a number."""

    _attr_native_step = 1
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        spec: dict[str, Any],
    ) -> None:
        """Set unit, a range from the value's kind, and slider-vs-box mode."""
        super().__init__(coordinator, sn, spec)
        if spec.get("unit"):
            self._attr_native_unit_of_measurement = spec["unit"]
        kind = spec.get("kind")
        low, high = _RANGE_BY_KIND.get(kind, _DEFAULT_RANGE)
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        # A percentage (brightness, volume) is a slider; open-ended values a box.
        self._attr_mode = NumberMode.SLIDER if kind in _SLIDER_KINDS else NumberMode.BOX

    @property
    def native_value(self) -> float | None:
        """The property's current numeric value."""
        v = self.prop_value
        return float(v) if isinstance(v, (int, float)) else None

    async def async_set_native_value(self, value: float) -> None:
        """Write the value (as an int when it's whole, to match the wire)."""
        await self.write(int(value) if value == int(value) else value)


class _SocLimit(NamedTuple):
    """
    A Solix SOC-limit slider descriptor.

    `key` is the telemetry (b5) value key; `param_key` the field in the HTTP
    get_solix_soc_params read; `write_kw` the api.py write keyword. Discharge = minimum
    SOC (floor); charge = maximum SOC (ceiling).
    """

    key: str
    param_key: str
    name: str
    icon: str
    low: int
    high: int
    write_kw: str


# `dischargeLimit` / `chargeLimit` are the b5-blob telemetry keys the SDK decodes and
# the bridge broadcasts (also echoed right after a write). Bounds keep them from
# crossing; the device still validates. Min SOC realistically sits low, max SOC high.
SOC_DISCHARGE = _SocLimit(
    "dischargeLimit",
    "dischargeLowerLimit",
    "Discharge Limit",
    "mdi:battery-arrow-down",
    0,
    20,
    "discharge",
)
SOC_CHARGE = _SocLimit(
    "chargeLimit",
    "chargeUpperLimit",
    "Charge Limit",
    "mdi:battery-arrow-up",
    80,
    100,
    "charge",
)


class EufySolixSocLimitNumber(EufySolixEntity, NumberEntity):
    """
    A Solarbank battery discharge/charge limit as a slider — reflects the DEVICE state.

    Setting it issues an `algo_ecdh` cloud write (`set_site_device_param`, param_type
    27) through the bridge, which is read-modify-write so the sibling limit and backup
    reserve are preserved. The shown value seeds from the bridge's `solix.devices`
    snapshot and updates on `solixReading` events carrying `dischargeLimit` /
    `chargeLimit` (decoded from the `b5` telemetry blob) — so a change made in the Anker
    app is reflected within ~seconds, and this is NOT assumed-state. A write we issue
    sets the value optimistically; the bridge echoes the merged result immediately and
    the next telemetry frame confirms. Device `solix:<sn>`, like the light/sensors.
    """

    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        limit: _SocLimit,
    ) -> None:
        """Bind to a Solix Solarbank limit; seed from its telemetry snapshot."""
        super().__init__(coordinator, sn, f"{limit.write_kw}_limit")
        self._limit = limit
        self._value = self._percent(self.solix_record.get("values") or {})
        self._attr_name = limit.name
        self._attr_icon = limit.icon
        self._attr_native_min_value = limit.low
        self._attr_native_max_value = limit.high

    def _percent(self, values: dict[str, Any]) -> float | None:
        """Return this limit's percent from `values` (None if absent or not numeric)."""
        v = values.get(self._limit.key)
        return float(v) if isinstance(v, (int, float)) else None

    @property
    def native_value(self) -> float | None:
        """The limit's current value (None until a reading or the HTTP read arrives)."""
        return self._value

    def _solix_update(self, values: dict[str, Any], *, snapshot: bool) -> bool:  # noqa: ARG002 - same rule for both sources
        """Adopt this limit's percent when a reading carries a new one."""
        v = self._percent(values)
        if v is None or v == self._value:
            return False
        self._value = v
        return True

    async def async_added_to_hass(self) -> None:
        """Track live readings/snapshot, then seed from the authoritative HTTP read."""
        await super().async_added_to_hass()
        # b5 telemetry only carries the limits on a settings frame (pushed on change,
        # not periodically), so seed from the cloud read + keep an HTTP backstop at the
        # user-configured cadence (CONF_SOC_REFRESH, default 60 s) — the slider shows
        # the real value on load and stays right when the push is quiet. Live b5 events
        # still update it immediately.
        await self._fetch_limit()
        secs = self._coordinator.config_entry.options.get(
            CONF_SOC_REFRESH, DEFAULT_SOC_REFRESH_SEC
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._timed_refresh, timedelta(seconds=secs)
            )
        )

    @callback
    def _timed_refresh(self, _now: Any) -> None:
        """Interval tick — re-read the SOC limits off the event loop."""
        self.hass.async_create_task(self._fetch_limit())

    async def _fetch_limit(self) -> None:
        """Read the authoritative SOC limits over HTTP and adopt this slider's value."""
        try:
            params = await self.client.get_solix_soc_params(self._sn)
        except Exception:  # noqa: BLE001 - a read hiccup must not break the entity
            return
        v = params.get(self._limit.param_key)
        if isinstance(v, (int, float)) and float(v) != self._value:
            self._value = float(v)
            self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Write the limit (whole-percent; optimistic — the bridge echo confirms)."""
        await self.client.set_solix_soc_limits(
            self._sn, **{self._limit.write_kw: int(value)}
        )
        self._value = float(int(value))
        self.async_write_ha_state()
