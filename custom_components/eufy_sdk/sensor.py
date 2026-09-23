"""Sensor platform — a diagnostic Info sensor plus one per read-only scalar property."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback

from .bespoke import BITFIELD_SWITCHES
from .const import CONF_HOST, EVENT_TYPE, LOGGER, SOLIX_READING_EVENT
from .entity import (
    EufySdkDeviceEntity,
    EufySdkPropertyEntity,
    EufySolixEntity,
    classify,
    has_capability,
    remove_stale_solix_entities,
    solix_devices_with,
)
from .light import LIGHT_HIDDEN_PROPS

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry

GO2RTC_RTSP_PORT = 8554  # go2rtc RTSP listener in the bridge image

# Nominal usable capacity of the Anker Solix Solarbank 4 E5000 Pro (AE103) — the "E5000"
# in the name. Used to derive the time-to-full / time-to-empty countdown from SOC + W.
# NOTE: expansion packs (each ~5000 Wh) are NOT accounted for yet; a stacked system runs
# longer than this single-pack figure implies.
SOLARBANK_CAPACITY_WH = 5000  # Wh

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
    "meterPowerL1": "channel_a8",
    "meterPowerL2": "channel_a9",
    "meterPowerL3": "channel_aa",
    "meterPowerTotal": "channel_ab",
    "meterImportEnergy": "channel_b3",
    "meterExportEnergy": "channel_b4",
}

# Anker Solix Solarbank (AE103 / gen-4) battery metrics. Unlike the meter, the SDK now
# emits these under NAMED keys (SOC/temp/power flows were live-correlated vs the app UI
# and bound in the decoder), so each metric reads its own key directly — no channel map.
# Signed fields (batteryPower/acPlugPower) use POWER, which HA renders with sign. Only
# confirmed fields are here; PV strings, AC current and export stay raw until confirmed.
SOLIX_BATTERY_METRICS: dict[str, dict[str, Any]] = {
    "batterySoc": {
        "name": "Battery",
        "device_class": SensorDeviceClass.BATTERY,
        "unit": "%",
        "precision": 0,
    },
    "batteryTemperature": {
        "name": "Battery Temperature",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": "°C",
        "precision": 0,
    },
    # State-of-health % from the SDK's a4 BMS-blob decode (candidate). No BATTERY
    # device_class: that means charge level; this is pack health, distinct from SOC.
    "batteryHealth": {
        "name": "Battery Health",
        "unit": "%",
        "icon": "mdi:battery-heart-variant",
        "precision": 0,
    },
    "batteryPower": {
        "name": "Battery Power",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:home-battery",
        "precision": 0,
    },
    "chargePower": {
        "name": "Charge Power",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:battery-charging",
        "precision": 0,
    },
    "dischargePower": {
        "name": "Discharge Power",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:battery-arrow-down",
        "precision": 0,
    },
    "acPlugPower": {
        "name": "AC Plug Power",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:power-plug",
        "precision": 0,
    },
    "gridInputPower": {
        "name": "Grid Input",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:transmission-tower",
        "precision": 0,
    },
    "homeLoadPower": {
        "name": "Home Load",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:home-lightning-bolt",
        "precision": 0,
    },
    "socketPower": {
        "name": "Socket Power",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:power-socket-uk",
        "precision": 0,
    },
    # Configured max home load (W), from state_info (= get_site_device_param max_load).
    "maxLoad": {
        "name": "Max Home Load",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:home-lightning-bolt-outline",
        "precision": 0,
        "enabled_default": False,
    },
    # Solar / PV input. `photovoltaicPower` (ff09 0xab) is the TOTAL across the strings;
    # `pv1Power`..`pv4Power` (0xc6..0xc9) are the per-string inputs, reading 0 while a
    # string is unused/dark — itself useful (you see which strings produce). C6 is
    # confirmed as a live solar input; all four are enabled by default.
    "photovoltaicPower": {
        "name": "Solar Power",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:solar-power",
        "precision": 0,
    },
    "pv1Power": {
        "name": "Solar Input 1",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:solar-panel",
        "precision": 0,
    },
    "pv2Power": {
        "name": "Solar Input 2",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:solar-panel",
        "precision": 0,
    },
    "pv3Power": {
        "name": "Solar Input 3",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:solar-panel",
        "precision": 0,
    },
    "pv4Power": {
        "name": "Solar Input 4",
        "device_class": SensorDeviceClass.POWER,
        "unit": "W",
        "icon": "mdi:solar-panel",
        "precision": 0,
    },
    # NOTE: the SOC discharge/charge limits are NOT sensors here — they are the writable
    # sliders on the number platform (EufySolixSocLimitNumber). The old read-only
    # sensors are retired via remove_stale_solix_entities in async_setup_entry.
}

# The Solarbank's operating (EMS) mode from the `state_info` `mode` value (live-mapped).
SOLIX_MODE_LABELS: dict[int, str] = {
    1: "Custom",
    2: "Self-Consumption",
    4: "Rapid Charging",
    7: "Smart",
    8: "Dynamic Tariff",
}


def _is_sensor(spec: dict) -> bool:
    """Return True for classify()=="sensor", plus bitfields with no bespoke switches."""
    kind = classify(spec)
    if kind == "sensor":
        return True
    return kind == "bitfield" and spec["name"] not in BITFIELD_SWITCHES


async def async_setup_entry(
    hass: HomeAssistant,
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
    entities.extend(
        EufyStreamUrlSensor(coordinator, sn, host)
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
    # these (kept off `data`). The named-metric map (SOLIX_METRICS / _METER_CHANNEL) is
    # the AE1X0 Smart Meter's ff09 tag layout, so it applies ONLY to an energyMeter
    # device. A Solarbank / battery reports different tags: it gets raw channels below.
    solix = getattr(coordinator, "solix_devices", {}) or {}
    energy_meters = [sn for sn, _ in solix_devices_with(coordinator, "energyMeter")]
    entities.extend(
        EufySolixSensor(coordinator, sn, metric, meta)
        for sn in energy_meters
        for metric, meta in SOLIX_METRICS.items()
    )
    # Solarbank / battery devices: named battery metrics (SOC, temp, power). The SDK
    # emits these under their own keys, so EufySolixSensor reads them directly.
    batteries = [sn for sn, _ in solix_devices_with(coordinator, "battery")]
    for sn in batteries:
        remove_stale_solix_entities(
            hass, "sensor", f"solix_{sn}_dischargeLimit", f"solix_{sn}_chargeLimit"
        )
    entities.extend(
        EufySolixSensor(coordinator, sn, metric, meta)
        for sn in batteries
        for metric, meta in SOLIX_BATTERY_METRICS.items()
    )
    # A Charging/Discharging/Idle status per battery (from signed battery power), with
    # an icon that reflects the state.
    entities.extend(EufySolixBatteryStatusSensor(coordinator, sn) for sn in batteries)
    # A live countdown per battery: time-to-full while charging, time-to-empty while
    # discharging (each derived from SOC + charge/discharge power against the pack
    # capacity; None outside its own mode).
    entities.extend(EufySolixTimeToFullSensor(coordinator, sn) for sn in batteries)
    entities.extend(EufySolixTimeToEmptySensor(coordinator, sn) for sn in batteries)
    # Operating (EMS) mode from the state_info push, as a labelled enum.
    entities.extend(EufySolixModeSensor(coordinator, sn) for sn in batteries)
    async_add_entities(entities)

    # Raw diagnostics for EVERY Solix device, added LAZILY as each raw key first
    # appears in a solixReading: `channel_<hex>` (param_info measurements not yet
    # named) AND `state_<hex>` (state_info SETTINGS tags not yet named — the
    # mapping-in-progress ones, e.g. the discharge/charge limits). The named metrics
    # above cover the confirmed fields; these expose the rest for correlation.
    # Disabled by default to avoid clutter.
    seen_channels: set[tuple[str, str]] = set()

    @callback
    def _add_new_channels(sn: str, values: dict) -> None:
        fresh = [
            ch
            for ch in values
            if ch.startswith(("channel_", "state_")) and (sn, ch) not in seen_channels
        ]
        for ch in fresh:
            seen_channels.add((sn, ch))
        if fresh:
            async_add_entities(
                EufySolixChannelSensor(coordinator, sn, ch) for ch in fresh
            )

    for sn, dev in solix.items():
        _add_new_channels(sn, dev.get("values") or {})

    @callback
    def _on_solix_reading(event: Event) -> None:
        if event.data.get("event") != SOLIX_READING_EVENT:
            return
        sn = event.data.get("deviceSn")
        if sn:
            _add_new_channels(sn, event.data.get("values") or {})

    entry.async_on_unload(hass.bus.async_listen(EVENT_TYPE, _on_solix_reading))


class EufySdkInfoSensor(EufySdkDeviceEntity, SensorEntity):
    """A diagnostic sensor: value = the device codec, attributes = its capabilities."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:information-outline"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Name it '<device> Info'."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_info"
        self._attr_translation_key = "info"

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

    _attr_translation_key = "last_person"
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
    ) -> None:
        """Bind to a camera serial and remember the bridge host for the URL."""
        super().__init__(coordinator, sn)
        self._host = host
        self._attr_unique_id = f"{sn}_stream_url"
        self._attr_translation_key = "stream_url"
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
        return f"rtsp://{self._host}:{GO2RTC_RTSP_PORT}/{self._sn}"


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
        self._attr_translation_key = "light_effect"

    @property
    def native_value(self) -> str | None:
        """The selected effect's name (`lightEffectId`), else `Effect <id>`."""
        rid = self.device.get("state", {}).get("lightEffectId")
        if not isinstance(rid, (int, float)):
            return None
        rid = int(rid)
        return self._name_by_id.get(rid) or f"Effect {rid}"


class EufySolixSensor(EufySolixEntity, SensorEntity):
    """
    A live telemetry sensor for an Anker Solix device.

    Seeds from the bridge's `solix.devices` snapshot and follows `solixReading` events
    (see `EufySolixEntity`); its HA device is distinct from any eufy device.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        metric: str,
        meta: dict[str, Any],
    ) -> None:
        """Bind to one (device, metric), described by its SOLIX_*METRICS entry."""
        # The SDK emits this quantity under its ff09 channel key, not the metric name
        # (only meterVoltageL1 is emitted named). Fall back to the metric name so a tag
        # that later graduates to a confirmed SDK name still resolves.
        self._source = _METER_CHANNEL.get(metric, metric)
        super().__init__(coordinator, sn, metric, watch=(self._source,))
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

    @property
    def native_value(self) -> float | int | None:
        """The latest value for this metric (None until a reading arrives)."""
        return self.solix_value(self._source)


class EufySolixChannelSensor(EufySolixEntity, SensorEntity):
    """
    A raw Solix telemetry channel (diagnostic, disabled by default).

    Exists so the meter's CT/measurement channels are visible for the load-correlation
    step — turn on a known load and watch which channel tracks it, then it graduates
    into SOLIX_METRICS as a named Power/Current/Energy sensor. No device_class or unit,
    since what the channel measures isn't known until it's correlated.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_suggested_display_precision = 2
    _track_snapshot = False

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        channel: str,
    ) -> None:
        """Bind to one (device, channel_<tag> | state_<tag>)."""
        super().__init__(coordinator, sn, channel, watch=(channel,))
        self._channel = channel
        # "channel_a8" -> "Channel A8"; "state_a5" -> "State A5"
        if channel.startswith("state_"):
            self._attr_name = f"State {channel.removeprefix('state_').upper()}"
        else:
            self._attr_name = f"Channel {channel.removeprefix('channel_').upper()}"

    @property
    def native_value(self) -> float | int | None:
        """The channel's latest raw value (None until a reading arrives)."""
        return self.solix_value(self._channel)


class EufySolixBatteryStatusSensor(EufySolixEntity, SensorEntity):
    """
    The Solarbank's charge state — Charging / Discharging / Idle.

    Derived from the signed battery power (`batteryPower`: positive charging, negative
    discharging), with an icon that reflects the state (a charging bolt, a down arrow,
    or a plain battery) so the device tile shows at a glance what the pack is doing.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = ["Charging", "Discharging", "Idle"]
    _attr_name = "Battery Status"
    _track_snapshot = False
    _IDLE_W = 5  # |power| below this reads Idle, so standby noise doesn't flap it

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a Solix battery device."""
        super().__init__(coordinator, sn, "battery_status", watch=("batteryPower",))

    @property
    def native_value(self) -> str | None:
        """Charging / Discharging / Idle from the sign of battery power."""
        p = self.solix_value("batteryPower")
        if p is None:
            return None
        if p > self._IDLE_W:
            return "Charging"
        if p < -self._IDLE_W:
            return "Discharging"
        return "Idle"

    @property
    def icon(self) -> str:
        """Icon that reflects the current charge state."""
        return {
            "Charging": "mdi:battery-charging",
            "Discharging": "mdi:battery-arrow-down",
            "Idle": "mdi:battery",
        }.get(self.native_value, "mdi:battery")


class EufySolixModeSensor(EufySolixEntity, SensorEntity):
    """
    The Solarbank's operating (EMS) mode, as a labelled enum-style string.

    Custom / Self-Consumption / Rapid Charging / Smart / Dynamic Tariff — from the
    `state_info` push (`mode`), mapped to a label (live-confirmed). An unknown code
    renders as `Mode <n>` rather than dropping, so a new firmware mode stays visible.
    """

    # Not an ENUM device_class: an unmapped code renders as "Mode <n>", which an ENUM
    # (value must be in _attr_options) would reject — a string sensor shows it fine.
    _attr_icon = "mdi:tune-variant"
    _attr_name = "Operating Mode"
    _track_snapshot = False

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a Solix battery device."""
        super().__init__(coordinator, sn, "mode", watch=("mode",))

    @property
    def native_value(self) -> str | None:
        """The mode label (or `Mode <n>` for an unmapped code; None until seen)."""
        mode = self.solix_value("mode")
        if mode is None:
            return None
        code = int(mode)
        return SOLIX_MODE_LABELS.get(code, f"Mode {code}")


class _EufySolixTimeSensor(EufySolixEntity, SensorEntity):
    """
    Base for the Solarbank time-to-full / time-to-empty countdown sensors.

    Both derive a countdown from the pack's state of charge and its charge/discharge
    power against the nominal capacity, so they watch the same four inputs. Each
    subclass turns them into an `H:MM:SS` countdown string in `native_value`; outside
    its own mode it reads None. It's a formatted string (not a numeric DURATION) so the
    tile reads as a clock rather than a raw minute count.
    """

    _attr_icon = "mdi:timer-sand"
    # Rate floor: below this the countdown blows up toward infinity (and standby noise
    # would make it jitter wildly), so we report Unknown instead.
    _MIN_POWER_W = 10
    _INPUTS = ("batterySoc", "batteryPower", "chargePower", "dischargePower")

    def __init__(
        self, coordinator: EufySdkDataUpdateCoordinator, sn: str, unique_key: str
    ) -> None:
        """Bind to a Solix battery device."""
        super().__init__(coordinator, sn, unique_key, watch=self._INPUTS)

    @staticmethod
    def _hms(hours: float) -> str:
        """Format an hours figure as an H:MM:SS string (e.g. 1:23:45, 10:05:00)."""
        total = max(0, round(hours * 3600))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"


class EufySolixTimeToFullSensor(_EufySolixTimeSensor):
    """
    Estimated time (H:MM:SS) until the Solarbank is fully charged (charging only).

    remaining_wh = (100 - SOC)/100 * capacity; hours = remaining_wh / charge_W.
    Reads None (Unknown) when not charging, when SOC ≥ 100, or when the charge rate
    is below `_MIN_POWER_W`. The rate prefers the unsigned `chargePower`, falling
    back to positive `batteryPower` when `chargePower` is missing/zero.
    """

    _attr_icon = "mdi:battery-charging"
    _attr_name = "Battery Time to Full"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a Solix battery device."""
        super().__init__(coordinator, sn, "time_to_full")

    def _charge_watts(self) -> float | None:
        """Charge rate: unsigned `chargePower`, else positive `batteryPower`."""
        cp = self.solix_value("chargePower")
        if cp is not None and cp > 0:
            return cp
        bp = self.solix_value("batteryPower")
        if bp is not None and bp > 0:
            return bp
        return None

    @property
    def native_value(self) -> str | None:
        """H:MM:SS to full while charging; None otherwise."""
        soc = self.solix_value("batterySoc")
        if soc is None or soc >= 100:  # noqa: PLR2004 - 100% = full, nothing to count
            return None
        watts = self._charge_watts()
        if watts is None or watts < self._MIN_POWER_W:
            return None
        remaining_wh = (100 - soc) / 100 * SOLARBANK_CAPACITY_WH
        return self._hms(remaining_wh / watts)


class EufySolixTimeToEmptySensor(_EufySolixTimeSensor):
    """
    Estimated time (H:MM:SS) until the Solarbank is empty (discharging only).

    used_wh = SOC/100 * capacity; hours = used_wh / discharge_W. Reads None
    (Unknown) when not discharging, when SOC ≤ 0, or when the discharge rate is
    below `_MIN_POWER_W`. The rate prefers the unsigned `dischargePower`, falling
    back to the magnitude of negative `batteryPower` when it's missing/zero.
    """

    _attr_icon = "mdi:battery-arrow-down"
    _attr_name = "Battery Time to Empty"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a Solix battery device."""
        super().__init__(coordinator, sn, "time_to_empty")

    def _discharge_watts(self) -> float | None:
        """Discharge rate: unsigned `dischargePower`, else |negative `batteryPower`|."""
        dp = self.solix_value("dischargePower")
        if dp is not None and dp > 0:
            return dp
        bp = self.solix_value("batteryPower")
        if bp is not None and bp < 0:
            return -bp
        return None

    @property
    def native_value(self) -> str | None:
        """H:MM:SS to empty while discharging; None otherwise."""
        soc = self.solix_value("batterySoc")
        if soc is None or soc <= 0:
            return None
        watts = self._discharge_watts()
        if watts is None or watts < self._MIN_POWER_W:
            return None
        used_wh = soc / 100 * SOLARBANK_CAPACITY_WH
        return self._hms(used_wh / watts)
