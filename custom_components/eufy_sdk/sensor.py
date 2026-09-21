"""Sensor platform — a diagnostic Info sensor plus one per read-only scalar property."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from .bespoke import BITFIELD_SWITCHES
from .const import (
    CONF_GO2RTC_RTSP_PORT,
    CONF_HOST,
    DEFAULT_GO2RTC_RTSP_PORT,
    DOMAIN,
    LOGGER,
)
from .entity import (
    EufySdkDeviceEntity,
    EufySdkPropertyEntity,
    classify,
    has_capability,
)
from .light import LIGHT_HIDDEN_PROPS

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry

EVENT_TYPE = f"{DOMAIN}_event"
# Anker Solix Smart Meter (AE1X0) telemetry metrics.
#
# IMPORTANT: the SDK only NAMES the one confirmed binding, `meterVoltageL1` (ff09 tag
# 0xAC). Every other quantity is emitted ONLY as a raw `channel_<hex>` key — the SDK
# deliberately won't assert an unconfirmed tag→name binding as a typed field. So we read
# each metric from its ff09 channel (see `_METER_CHANNEL`), not from a name the SDK does
# not emit; else every sensor but Voltage L1 reads an absent key and shows "Unknown".
# The tag→channel bindings are the app's own field list (structural inference on a
# single-phase / single-CT install, where only L1 + totals carry data), so L2/L3 and the
# not-yet-scale-confirmed energy counters ship disabled-by-default; a 3-phase user can
# enable them. The sensor machinery below is generic over this map.
SOLIX_METRICS: dict[str, dict[str, Any]] = {
    "meterVoltageL1": {
        "name": "Voltage L1",
        "device_class": SensorDeviceClass.VOLTAGE,
        "unit": "V",
        "icon": "mdi:sine-wave",
        "precision": 2,  # float32 (236.8999…); show 2 dp
    },
    "meterVoltageL2": {
        "name": "Voltage L2",
        "device_class": SensorDeviceClass.VOLTAGE,
        "unit": "V",
        "icon": "mdi:sine-wave",
        "precision": 2,
        "enabled_default": False,
    },
    "meterVoltageL3": {
        "name": "Voltage L3",
        "device_class": SensorDeviceClass.VOLTAGE,
        "unit": "V",
        "icon": "mdi:sine-wave",
        "precision": 2,
        "enabled_default": False,
    },
    "meterCurrentL1": {
        "name": "Current L1",
        "device_class": SensorDeviceClass.CURRENT,
        "unit": "A",
        "icon": "mdi:current-ac",
        "precision": 2,
    },
    "meterCurrentL2": {
        "name": "Current L2",
        "device_class": SensorDeviceClass.CURRENT,
        "unit": "A",
        "icon": "mdi:current-ac",
        "precision": 2,
        "enabled_default": False,
    },
    "meterCurrentL3": {
        "name": "Current L3",
        "device_class": SensorDeviceClass.CURRENT,
        "unit": "A",
        "icon": "mdi:current-ac",
        "precision": 2,
        "enabled_default": False,
    },
    "meterCurrentTotal": {
        "name": "Current Total",
        "device_class": SensorDeviceClass.CURRENT,
        "unit": "A",
        "icon": "mdi:current-ac",
        "precision": 2,
    },
    "meterPowerL1": {
        "name": "Power L1",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:flash",
        "precision": 1,
    },
    "meterPowerL2": {
        "name": "Power L2",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:flash",
        "precision": 1,
        "enabled_default": False,
    },
    "meterPowerL3": {
        "name": "Power L3",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:flash",
        "precision": 1,
        "enabled_default": False,
    },
    "meterPowerTotal": {
        "name": "Power Total",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:flash",
        "precision": 1,
    },
    # Energy counters: cumulative, so TOTAL_INCREASING — but the unit scale (Wh vs kWh)
    # isn't confirmed yet, so disabled-by-default to keep them out of the Energy
    # Dashboard / long-term statistics until a load capture pins the scale.
    "meterImportEnergy": {
        "name": "Imported Energy",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": "Wh",
        "icon": "mdi:transmission-tower-export",
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "precision": 0,
        "enabled_default": False,
    },
    "meterExportEnergy": {
        "name": "Exported Energy",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": "Wh",
        "icon": "mdi:transmission-tower-import",
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "precision": 0,
        "enabled_default": False,
    },
}

# The SDK telemetry key each metric reads: its ff09 tag as the `channel_<hex>` key the
# decoder emits. Voltage L1 is emitted BOTH named (`meterVoltageL1`) and raw
# (`channel_ac`); reading the channel is uniform and future-proof (the decoder always
# emits `channel_<hex>`, even after a tag graduates to a confirmed name).
_METER_CHANNEL: dict[str, str] = {
    "meterVoltageL1": "channel_ac",
    "meterVoltageL2": "channel_ad",
    "meterVoltageL3": "channel_ae",
    "meterCurrentL1": "channel_af",
    "meterCurrentL2": "channel_b0",
    "meterCurrentL3": "channel_b1",
    "meterCurrentTotal": "channel_b2",
    "meterPowerL1": "channel_a8",
    "meterPowerL2": "channel_a9",
    "meterPowerL3": "channel_aa",
    "meterPowerTotal": "channel_ab",
    "meterImportEnergy": "channel_b3",
    "meterExportEnergy": "channel_b4",
}

# Tags the SDK does not name yet (b5/b6/b7 — the app's own decoder names no field for
# them; b7 ≈ 0.1 at idle, a firmware-level power-factor candidate). Exposed as raw
# DIAGNOSTIC sensors (disabled by default) so they're visible for correlation; they
# graduate into SOLIX_METRICS once identified.
SOLIX_METER_RAW_CHANNELS = [f"channel_{tag}" for tag in ("b5", "b6", "b7")]


def _is_sensor(spec: dict) -> bool:
    """Return True for classify()=="sensor", plus bitfields with no bespoke switches."""
    kind = classify(spec)
    if kind == "sensor":
        return True
    return kind == "bitfield" and spec["name"] not in BITFIELD_SWITCHES


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create an Info sensor per device, plus a sensor per property routed here."""
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [
        EufySdkInfoSensor(coordinator, sn) for sn in coordinator.data
    ]
    entities.extend(
        EufySdkPropertySensor(coordinator, sn, spec)
        for sn in coordinator.data
        for spec in entry.runtime_data.properties.get(sn, [])
        if _is_sensor(spec)
        # A smart_light's effect internals are represented by the light's effect picker.
        and not (
            spec["name"] in LIGHT_HIDDEN_PROPS
            and has_capability(coordinator.data[sn], "smart_light")
        )
    )
    # A "Last person" sensor for AI face recognition — surfaces the recognized name.
    entities.extend(
        EufySdkLastPersonSensor(coordinator, sn)
        for sn, dev in coordinator.data.items()
        if has_capability(dev, "person_detection")
    )
    # A "Stream URL" sensor per camera — the RTSP URL while a live feed is active.
    host = entry.data[CONF_HOST]
    rtsp_port = int(entry.data.get(CONF_GO2RTC_RTSP_PORT, DEFAULT_GO2RTC_RTSP_PORT))
    entities.extend(
        EufyStreamUrlSensor(coordinator, sn, host, rtsp_port)
        for sn, dev in coordinator.data.items()
        if dev.get("stream")
    )
    # A "Light Effect" sensor per smart_light — the selected effect BY NAME (from the
    # gallery), not the raw id. Fetch the catalogue once (bridge-cached); on failure the
    # sensor falls back to rendering the id as "Effect <n>".
    smart_lights = [
        sn for sn, dev in coordinator.data.items() if has_capability(dev, "smart_light")
    ]
    if smart_lights:
        name_by_id: dict[int, str] = {}
        try:
            for e in await entry.runtime_data.client.list_effects():
                if e.get("name"):
                    name_by_id[e["id"]] = e["name"]
        except Exception:  # noqa: BLE001 - names are optional; the sensor falls back to the id
            LOGGER.debug(
                "effect names unavailable for Light Effect sensor", exc_info=True
            )
        entities.extend(
            EufyLightEffectSensor(coordinator, sn, name_by_id) for sn in smart_lights
        )
    # Anker Solix (separate account): a sensor per known metric on each energy-meter
    # device. Live values arrive as `solixReading` events; the eufy platforms never see
    # these (kept off `data`).
    solix = getattr(coordinator, "solix_devices", {}) or {}
    energy_meters = [
        sn for sn, dev in solix.items() if "energyMeter" in dev.get("capabilities", [])
    ]
    entities.extend(
        EufySolixSensor(coordinator, sn, metric, meta)
        for sn in energy_meters
        for metric, meta in SOLIX_METRICS.items()
    )
    # Raw CT/measurement channels as diagnostics — visible for the load-correlation
    # step, until each is named. Disabled by default so they don't clutter.
    entities.extend(
        EufySolixChannelSensor(coordinator, sn, channel)
        for sn in energy_meters
        for channel in SOLIX_METER_RAW_CHANNELS
    )
    async_add_entities(entities)


class EufySdkInfoSensor(EufySdkDeviceEntity, SensorEntity):
    """A diagnostic sensor: value = the device codec, attributes = its capabilities."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:information-outline"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Name it '<device> Info'."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_info"
        self._attr_name = "Info"

    @property
    def native_value(self) -> str | None:
        """The device's wire-protocol family (camera / station / sensor / …)."""
        return self.device.get("codec")

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the capability list + serial for a host to inspect."""
        return {"serial": self._sn, "capabilities": self.device.get("capabilities", [])}


class EufySdkPropertySensor(EufySdkPropertyEntity, SensorEntity):
    """A read-only number / string / enum property as a sensor."""

    # Read-only telemetry (battery, wifi, firmware, codes, unsplit bitfields) belongs
    # under Diagnostics, not the main Controls area — matching the old eufy integration.
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        spec: dict[str, Any],
    ) -> None:
        """Set unit + device/state class from the value's kind."""
        super().__init__(coordinator, sn, spec)
        if spec.get("unit"):
            self._attr_native_unit_of_measurement = spec["unit"]
        kind = spec.get("kind")
        if kind == "percent":
            self._attr_state_class = SensorStateClass.MEASUREMENT
            if "battery" in self._prop.lower():
                self._attr_device_class = SensorDeviceClass.BATTERY
        elif kind == "dbm":
            self._attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif kind == "celsius":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> Any:
        """Current value; enums render as their label, structured values are dropped."""
        v = self.prop_value
        if v is None:
            return None
        enum = self._spec.get("enumValues")
        if enum:
            return enum.get(str(v), v) if isinstance(v, (int, str)) else None
        return v if isinstance(v, (int, float, str)) else None


class EufySdkLastPersonSensor(EufySdkDeviceEntity, SensorEntity):
    """
    The most recent AI face-recognition result for a camera.

    A `personDetected` push carries only a numeric `person_id`; the bridge resolves
    it against the HomeBase face roster and adds `person_name` (+ `recognized`). So a
    match shows the person's name, an unmatched face shows Unknown, and an explicit
    `strangerDetected` shows Stranger — letting automations key on "who" was seen,
    not just "a person".
    """

    _attr_name = "Last person"
    _attr_icon = "mdi:account-question"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a person-detection-capable device."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_last_person"

    async def async_added_to_hass(self) -> None:
        """Subscribe to bridge detection events while registered."""
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_TYPE, self._handle_event))

    @callback
    def _handle_event(self, event: Event) -> None:
        """Record the recognized name (or Unknown/Stranger) from a detection."""
        data = event.data
        if data.get("deviceSn") != self._sn:
            return
        ev = data.get("event")
        if ev == "strangerDetected":
            name, recognized, person_id = "Stranger", False, None
        elif ev == "personDetected":
            person_name = data.get("person_name")
            recognized = bool(data.get("recognized")) and bool(person_name)
            name = person_name if recognized else "Unknown"
            person_id = data.get("person_id")
        else:
            return
        self._attr_native_value = name
        self._attr_extra_state_attributes = {
            "recognized": recognized,
            "person_id": person_id,
            "event": ev,
        }
        self.async_write_ha_state()


class EufyStreamUrlSensor(EufySdkDeviceEntity, SensorEntity):
    """The camera's RTSP URL while streaming or rtspStream is on; else empty."""

    _attr_icon = "mdi:link-variant"

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        host: str,
        port: int,
    ) -> None:
        """Bind to a camera serial and remember the bridge host for the URL."""
        super().__init__(coordinator, sn)
        self._host = host
        self._port = port
        self._attr_unique_id = f"{sn}_stream_url"
        self._attr_name = "Stream URL"
        self._active: bool | None = None  # last streamState event; None → use poll

    async def async_added_to_hass(self) -> None:
        """Subscribe to the bridge's streamState events for this device."""
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_TYPE, self._handle_event))

    @callback
    def _handle_event(self, event: Event) -> None:
        """Track this device's streamState (on/off)."""
        data = event.data
        if data.get("deviceSn") != self._sn or data.get("event") != "streamState":
            return
        self._active = bool(data.get("active"))
        self.async_write_ha_state()

    @property
    def _streaming(self) -> bool:
        """Event value once seen; else the device-list `streaming` flag."""
        if self._active is not None:
            return self._active
        return bool(self.device.get("streaming"))

    @property
    def native_value(self) -> str | None:
        """The RTSP URL while streaming or while rtspStream is on; else None."""
        rtsp_on = self.device.get("state", {}).get("rtspStream") is True
        if not self._streaming and not rtsp_on:
            return None
        return f"rtsp://{self._host}:{self._port}/{self._sn}"


class EufyLightEffectSensor(EufySdkDeviceEntity, SensorEntity):
    """The smart-light's selected effect, by name (falls back to the raw id)."""

    _attr_icon = "mdi:palette"

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        name_by_id: dict[int, str],
    ) -> None:
        """Bind to a smart-light serial with the effect id->name map."""
        super().__init__(coordinator, sn)
        self._name_by_id = name_by_id
        self._attr_unique_id = f"{sn}_light_effect"
        self._attr_name = "Light Effect"

    @property
    def native_value(self) -> str | None:
        """The selected effect's name (`lightEffectId`), else `Effect <id>`."""
        rid = self.device.get("state", {}).get("lightEffectId")
        if not isinstance(rid, (int, float)):
            return None
        rid = int(rid)
        return self._name_by_id.get(rid) or f"Effect {rid}"


class EufySolixSensor(SensorEntity):
    """
    A live telemetry sensor for an Anker Solix device.

    Solix is a separate account/backend, so these are NOT coordinator (device-list)
    entities: the initial value seeds from the bridge's `solix.devices` snapshot, and
    updates arrive as `solixReading` events. Their HA device is distinct from any eufy
    device (identifier `solix:<sn>`).
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        metric: str,
        meta: dict[str, Any],
    ) -> None:
        """Bind to one (device, metric); build its Anker Solix HA device_info."""
        self._coordinator = coordinator
        self._sn = sn
        self._metric = metric
        # The SDK emits this quantity under its ff09 channel key, not the metric name
        # (only meterVoltageL1 is emitted named). Fall back to the metric name so a tag
        # that later graduates to a confirmed SDK name still resolves.
        self._source = _METER_CHANNEL.get(metric, metric)
        dev = coordinator.solix_devices.get(sn, {})
        self._value = (dev.get("values") or {}).get(self._source)
        self._attr_unique_id = f"solix_{sn}_{metric}"
        self._attr_name = meta["name"]
        self._attr_native_unit_of_measurement = meta.get("unit")
        if meta.get("device_class"):
            self._attr_device_class = meta["device_class"]
        if meta.get("state_class"):
            self._attr_state_class = meta["state_class"]
        if meta.get("icon"):
            self._attr_icon = meta["icon"]
        if meta.get("precision") is not None:
            self._attr_suggested_display_precision = meta["precision"]
        if meta.get("enabled_default") is False:
            self._attr_entity_registry_enabled_default = False
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"solix:{sn}")},
            name=dev.get("name") or sn,
            manufacturer="Anker Solix",
            model=dev.get("productCode"),
            sw_version=dev.get("firmware"),
            serial_number=sn,
        )

    @property
    def native_value(self) -> float | int | None:
        """The latest value for this metric (None until a reading arrives)."""
        return self._value

    @property
    def available(self) -> bool:
        """Available while the bridge still lists this Solix device."""
        return self._sn in getattr(self._coordinator, "solix_devices", {})

    async def async_added_to_hass(self) -> None:
        """Subscribe to bridge events for live `solixReading` updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_TYPE, self._handle_event))

    @callback
    def _handle_event(self, event: Event) -> None:
        """Update from a `solixReading` for this device that carries our metric."""
        data = event.data
        if data.get("event") != "solixReading" or data.get("deviceSn") != self._sn:
            return
        values = data.get("values") or {}
        if self._source in values:
            self._value = values[self._source]
            self.async_write_ha_state()


class EufySolixChannelSensor(SensorEntity):
    """
    A raw Solix telemetry channel (diagnostic, disabled by default).

    Exists so the meter's CT/measurement channels are visible for the load-correlation
    step — turn on a known load and watch which channel tracks it, then it graduates
    into SOLIX_METRICS as a named Power/Current/Energy sensor. No device_class or unit,
    since what the channel measures isn't known until it's correlated.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        channel: str,
    ) -> None:
        """Bind to one (device, channel_<tag>)."""
        self._coordinator = coordinator
        self._sn = sn
        self._channel = channel
        dev = coordinator.solix_devices.get(sn, {})
        self._value = (dev.get("values") or {}).get(channel)
        self._attr_unique_id = f"solix_{sn}_{channel}"
        # "channel_a8" -> "Channel A8"
        self._attr_name = f"Channel {channel.removeprefix('channel_').upper()}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"solix:{sn}")},
            name=dev.get("name") or sn,
            manufacturer="Anker Solix",
            model=dev.get("productCode"),
            sw_version=dev.get("firmware"),
            serial_number=sn,
        )

    @property
    def native_value(self) -> float | int | None:
        """The channel's latest raw value (None until a reading arrives)."""
        return self._value

    @property
    def available(self) -> bool:
        """Available while the bridge still lists this Solix device."""
        return self._sn in getattr(self._coordinator, "solix_devices", {})

    async def async_added_to_hass(self) -> None:
        """Subscribe to bridge events for live channel updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_TYPE, self._handle_event))

    @callback
    def _handle_event(self, event: Event) -> None:
        """Update from a `solixReading` for this device that carries our channel."""
        data = event.data
        if data.get("event") != "solixReading" or data.get("deviceSn") != self._sn:
            return
        values = data.get("values") or {}
        if self._channel in values:
            self._value = values[self._channel]
            self.async_write_ha_state()
