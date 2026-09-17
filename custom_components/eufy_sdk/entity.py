"""Base entity for eufy_sdk — one HA device per eufy device (keyed by serial)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import EufySdkDataUpdateCoordinator

if TYPE_CHECKING:
    from collections.abc import Callable

# A device→cloud settings change (e.g. camera enable/disable) lags the P2P write
# by a few seconds. So on write we hold the just-written value optimistically and
# reconcile via a delayed cloud re-pull: the hold is released ACTIVELY after that
# pull (not on a timeout), so the entity always re-renders to the true state — if
# the write didn't take, it reverts to the real value then.
POST_WRITE_REFRESH_SECS = 20


class EufySdkDeviceEntity(CoordinatorEntity[EufySdkDataUpdateCoordinator]):
    """An entity attached to one eufy device (`sn`), from the bridge device list."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a device serial and build its HA device_info."""
        super().__init__(coordinator)
        self._sn = sn
        dev = coordinator.data.get(sn, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, sn)},
            name=dev.get("name") or sn,  # the user's device name (e.g. "Dining room")
            manufacturer="eufy",
            model=dev.get("model") or dev.get("codec"),  # the model code, not the codec
            serial_number=sn,
        )

    @property
    def device(self) -> dict:
        """Return the latest device record (sn/name/codec/capabilities/stream)."""
        return self.coordinator.data.get(self._sn, {})

    @property
    def available(self) -> bool:
        """Available while the bridge still reports this device."""
        return super().available and self._sn in self.coordinator.data


def label_for(prop: str) -> str:
    """Turn a camelCase name into a human label ('statusLed' -> 'Status Led')."""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", prop)
    return spaced[:1].upper() + spaced[1:]


def has_capability(record: dict | None, capability: str) -> bool:
    """Whether a device record (from the bridge device list) declares a capability."""
    return capability in (record or {}).get("capabilities", [])


# Properties that stay a PRIMARY control (no entity_category), so they sit in the
# device's main Controls area rather than under Configuration. Everything else writable
# is a setting — the old eufy integration kept only enable/disable up top.
PRIMARY_CONTROL_PROPS = frozenset({"enabled"})


def is_setting(prop: str) -> bool:
    """Return True when a writable property is a setting, not a primary control."""
    return prop not in PRIMARY_CONTROL_PROPS


def classify(spec: dict[str, Any]) -> str | None:
    """
    Route one property spec to exactly one platform, so no two platforms claim it.

    A `kind: "bitfield"` (e.g. `aiDetectType`) is never a scalar you'd nudge — it's a
    pack of bits — so it routes to "bitfield" for bespoke handling (see bespoke.py):
    known ones become per-bit switches, unknown ones a read-only sensor.

    A writable number is only a `number` when it has a real scale (`kind` other than
    bitfield, or a `unit`) — a bounded quantity you'd adjust (brightness %, a timer).
    Otherwise it's an opaque code and becomes a read-only sensor, as does a writable
    enum with no options to choose from.
    """
    t, writable, kind = spec.get("type"), spec.get("writable"), spec.get("kind")
    if kind == "bitfield":
        return "bitfield"
    # A fixed set of choices (enumValues) is a select when writable, a labelled sensor
    # otherwise — regardless of the wire `type`, since some enums ride a numeric param
    # (e.g. hubAlarmTone is type "number", kind "enum").
    if spec.get("enumValues"):
        return "select" if writable else "sensor"
    has_scale = bool(kind or spec.get("unit"))
    if t == "bool":
        return "switch" if writable else "binary_sensor"
    if t == "number":
        return "number" if (writable and has_scale) else "sensor"
    if t in ("string", "enum"):
        return "sensor"
    return None


class EufySdkPropertyEntity(EufySdkDeviceEntity):
    """An entity bound to one property, reading its live value from the `state` map."""

    # Subclasses that name themselves through a translation key set this. Home
    # Assistant lets `_attr_name` win over a translation key, so a subclass that
    # sets both would silently show the property's own label instead of its name —
    # every per-bit switch reading "Ai Detect Type", say.
    _named_by_translation = False

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        spec: dict[str, Any],
    ) -> None:
        """Bind to a property spec ({name, type, unit, kind, writable, enumValues})."""
        super().__init__(coordinator, sn)
        self._spec = spec
        self._prop: str = spec["name"]
        self._attr_unique_id = f"{sn}_{self._prop}"
        if not self._named_by_translation:
            self._attr_name = label_for(self._prop)
        self._post_write_unsub: Callable[[], None] | None = None
        self._assumed_value: Any = None

    @property
    def prop_value(self) -> Any:
        """The held optimistic value if set, else the device's live state."""
        if self._assumed_value is not None:
            return self._assumed_value
        return self.device.get("state", {}).get(self._prop)

    async def write(self, value: Any) -> None:
        """Write, hold the value optimistically, and reconcile via a delayed pull."""
        client = self.coordinator.config_entry.runtime_data.client
        await client.set_property(self._sn, self._prop, value)
        # Keep the intended value shown until the delayed pull reconciles it.
        self._assumed_value = value
        self.async_write_ha_state()
        # Schedule one delayed re-pull (replacing any pending) so a slow change
        # is reflected without waiting for the next scheduled poll.
        if self._post_write_unsub is not None:
            self._post_write_unsub()
        self._post_write_unsub = async_call_later(
            self.hass, POST_WRITE_REFRESH_SECS, self._post_write_refresh
        )

    @callback
    def _post_write_refresh(self, _now: Any) -> None:
        """Fire the delayed post-write reconcile."""
        self._post_write_unsub = None
        self.hass.async_create_task(self._reconcile())

    async def _reconcile(self) -> None:
        """Pull fresh cloud state, drop the optimistic hold, re-render to truth."""
        await self.coordinator.async_request_refresh()
        self._assumed_value = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a pending delayed refresh when the entity goes away."""
        if self._post_write_unsub is not None:
            self._post_write_unsub()
            self._post_write_unsub = None
        self._assumed_value = None
        await super().async_will_remove_from_hass()
