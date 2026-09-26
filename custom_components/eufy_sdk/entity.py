"""Base entity for eufy_sdk — one HA device per eufy device (keyed by serial)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, EVENT_TYPE, SOLIX_READING_EVENT
from .coordinator import EufySdkDataUpdateCoordinator

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from homeassistant.core import Event, HomeAssistant

    from .api import EufySdkApiClient


@callback
def remove_stale_solix_entities(
    hass: HomeAssistant, platform: str, *unique_ids: str
) -> None:
    """
    Drop retired Solix entities from the registry by unique_id, if present.

    Superseded Solix entities (e.g. a read-only sensor replaced by a writable slider,
    or a select replaced by it) leave a registry row that otherwise lingers as an
    unavailable entity after upgrade; each platform's setup calls this to clear its own.
    """
    registry = er.async_get(hass)
    for uid in unique_ids:
        entity_id = registry.async_get_entity_id(platform, DOMAIN, uid)
        if entity_id:
            registry.async_remove(entity_id)


def solix_devices_with(
    coordinator: EufySdkDataUpdateCoordinator, capability: str
) -> Iterator[tuple[str, dict]]:
    """
    Yield `(sn, record)` for each Solix device advertising `capability`.

    Solix is a separate account/backend, kept off the coordinator's main `data` under
    `solix_devices` (absent unless the bridge has SOLIX_* configured). Every platform's
    setup filters that map to the devices it builds entities for — a Solarbank by
    `"battery"`, the meter by `"energyMeter"` — so this centralises the `getattr` guard
    and the capability test they'd otherwise each repeat.
    """
    solix = getattr(coordinator, "solix_devices", {}) or {}
    for sn, dev in solix.items():
        if capability in dev.get("capabilities", []):
            yield sn, dev


def solix_device_info(coordinator: EufySdkDataUpdateCoordinator, sn: str) -> DeviceInfo:
    """
    Build the HA device for an Anker Solix serial.

    Solix devices get their own `solix:<sn>` identifier so they never merge with a eufy
    device, and every Solix entity must build exactly this so they share one device.
    """
    dev = coordinator.solix_devices.get(sn, {})
    return DeviceInfo(
        identifiers={(DOMAIN, f"solix:{sn}")},
        name=dev.get("name") or sn,
        manufacturer="Anker Solix",
        model=dev.get("productCode"),
        sw_version=dev.get("firmware"),
        serial_number=sn,
    )


class EufySolixEntity(Entity):
    """
    An entity on one Anker Solix device, fed by its telemetry.

    Solix is a separate account/backend, so these are not coordinator (device-list)
    entities: values seed from the bridge's `solix.devices` snapshot and then arrive as
    `solixReading` bus events. The snapshot is also polled by the coordinator, and with
    `_track_snapshot` the entity adopts it too — a backstop for a metric that rides an
    infrequent frame, whose live event may have been missed.

    Each subclass names the telemetry keys it `watch`es; the latest value of each is
    kept in `_solix_values`, and a change re-renders the entity. A subclass whose keys
    need more than "keep the latest value" overrides `_solix_update`.
    """

    _attr_has_entity_name = True
    # Whether to also adopt values from the coordinator's polled Solix snapshot.
    _track_snapshot = True

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        unique_key: str,
        watch: Iterable[str] = (),
    ) -> None:
        """Bind to a Solix serial, seeding the watched values from its snapshot."""
        self._coordinator = coordinator
        self._sn = sn
        self._watch = tuple(watch)
        values = self.solix_record.get("values") or {}
        self._solix_values: dict[str, Any] = {k: values.get(k) for k in self._watch}
        self._attr_unique_id = f"solix_{sn}_{unique_key}"
        self._attr_device_info = solix_device_info(coordinator, sn)

    @property
    def solix_record(self) -> dict[str, Any]:
        """The latest `solix.devices` record for this serial ({} once it is gone)."""
        return self._coordinator.solix_devices.get(self._sn, {})

    @property
    def client(self) -> EufySdkApiClient:
        """The bridge client this entity's config entry talks through."""
        return self._coordinator.config_entry.runtime_data.client

    @property
    def available(self) -> bool:
        """Available while the bridge still lists this Solix device."""
        return self._sn in getattr(self._coordinator, "solix_devices", {})

    def solix_value(self, key: str) -> Any:
        """Return the latest value of a watched key (None until it has been seen)."""
        return self._solix_values.get(key)

    async def async_added_to_hass(self) -> None:
        """Subscribe to live readings (and the coordinator snapshot, if tracked)."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_TYPE, self._handle_solix_event)
        )
        if self._track_snapshot:
            self.async_on_remove(
                self._coordinator.async_add_listener(self._handle_solix_snapshot)
            )
            self._handle_solix_snapshot()

    @callback
    def _handle_solix_event(self, event: Event) -> None:
        """Apply a `solixReading` addressed to this device."""
        data = event.data
        if data.get("event") != SOLIX_READING_EVENT or data.get("deviceSn") != self._sn:
            return
        if self._solix_update(data.get("values") or {}, snapshot=False):
            self.async_write_ha_state()

    @callback
    def _handle_solix_snapshot(self) -> None:
        """Apply the coordinator's polled snapshot of this device."""
        if self._solix_update(self.solix_record.get("values") or {}, snapshot=True):
            self.async_write_ha_state()

    def _solix_update(self, values: dict[str, Any], *, snapshot: bool) -> bool:
        """
        Adopt the watched keys present in `values`; return True if any changed.

        A snapshot holds the bridge's last known value per key, where None means "not
        seen", so a None there never overwrites a value the entity already has.
        """
        changed = False
        for key in self._watch:
            if key not in values or (snapshot and values[key] is None):
                continue
            if values[key] != self._solix_values.get(key):
                self._solix_values[key] = values[key]
                changed = True
        return changed


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
    def client(self) -> EufySdkApiClient:
        """The bridge client this entity's config entry talks through."""
        return self.coordinator.config_entry.runtime_data.client

    # Entities that exist to show the offline state itself (the Online sensor) opt out.
    _follows_device_online = True

    @property
    def available(self) -> bool:
        """Available while the bridge still reports this device and it isn't offline."""
        return (
            super().available
            and self._sn in self.coordinator.data
            and not (self._follows_device_online and device_offline(self.device))
        )


def device_offline(record: dict | None) -> bool:
    """
    Whether the cloud reports this device offline (`deviceStatus` is False).

    Only devices that report `deviceStatus` (battery cameras, HomeBase-attached
    devices) can be offline here. Where the key is missing the state is unknown,
    and unknown is not offline, so those devices stay available as before.
    """
    return (record or {}).get("state", {}).get("deviceStatus") is False


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
        await self.client.set_property(self._sn, self._prop, value)
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
