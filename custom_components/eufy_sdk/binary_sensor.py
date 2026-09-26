"""Binary sensor platform — one per read-only boolean property (motion, contact, …)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import EVENT_TYPE
from .coordinator import EufySdkDataUpdateCoordinator
from .entity import (
    EufySdkDeviceEntity,
    EufySdkPropertyEntity,
    classify,
    solix_device_info,
)
from .pushmap import (
    MOTION_EVENTS,
    PACKAGE_CLEARED_EVENT,
    PACKAGE_PRESENT_EVENTS,
    PUSH_AUTO_OFF_SECONDS,
    PUSH_BINARY_SENSORS,
)

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .data import EufySdkConfigEntry


# Infer a device_class from the property name (substring match, first hit wins).
_DEVICE_CLASS_BY_NAME: list[tuple[str, BinarySensorDeviceClass]] = [
    ("motion", BinarySensorDeviceClass.MOTION),
    ("person", BinarySensorDeviceClass.OCCUPANCY),
    ("contact", BinarySensorDeviceClass.OPENING),
    ("door", BinarySensorDeviceClass.DOOR),
    ("charging", BinarySensorDeviceClass.BATTERY_CHARGING),
    ("battery", BinarySensorDeviceClass.BATTERY),
    ("sound", BinarySensorDeviceClass.SOUND),
    ("online", BinarySensorDeviceClass.CONNECTIVITY),
]


def _device_class(name: str) -> BinarySensorDeviceClass | None:
    low = name.lower()
    return next((dc for key, dc in _DEVICE_CLASS_BY_NAME if key in low), None)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a binary sensor per read-only bool property, plus push-driven ones."""
    coordinator = entry.runtime_data.coordinator
    entities: list[BinarySensorEntity] = [
        EufySdkBinarySensor(coordinator, sn, spec)
        for sn in coordinator.data
        for spec in entry.runtime_data.properties.get(sn, [])
        if classify(spec) == "binary_sensor"
    ]
    # Push-driven sensors (motion / person / ringing): flipped ON in real time by the
    # SDK's push channel, then auto-OFF (push has no "cleared" signal). Gated on
    # capability.
    for sn, dev in coordinator.data.items():
        caps = set(dev.get("capabilities", []))
        for bus_event, (key, name, device_class, cap) in PUSH_BINARY_SENSORS.items():
            if cap in caps:
                # Motion is an umbrella over all visual detections (these cams classify
                # motion as person/vehicle/… and may never emit a bare "motion"); others
                # match just their own event.
                events = (
                    MOTION_EVENTS if bus_event == "motion" else frozenset({bus_event})
                )
                entities.append(
                    EufyPushBinarySensor(
                        coordinator, sn, events, (key, name, device_class)
                    )
                )
    # A "Streaming" sensor per camera: ON while the bridge holds a live P2P feed
    # (go2rtc pulling /stream). Edge-driven by the bridge's `streamState` event,
    # with the device-list poll as the initial/reconnect value.
    entities.extend(
        EufyStreamingBinarySensor(coordinator, sn)
        for sn, dev in coordinator.data.items()
        if dev.get("stream")
    )
    # A "Package" sensor per doorbell: latched by the package push events.
    entities.extend(
        EufyPackageBinarySensor(coordinator, sn)
        for sn, dev in coordinator.data.items()
        if "doorbell" in dev.get("capabilities", [])
    )
    # An "Online" sensor per device that reports the cloud's `deviceStatus` (battery
    # cameras, HomeBase-attached devices). The device's other entities go unavailable
    # while it is offline; this one stays available so the offline state is visible.
    entities.extend(
        EufyOnlineBinarySensor(coordinator, sn)
        for sn, dev in coordinator.data.items()
        if dev.get("state", {}).get("deviceStatus") is not None
    )
    # Anker Solix (separate account): a Wi-Fi connectivity sensor per device (polled).
    solix = getattr(coordinator, "solix_devices", {}) or {}
    entities.extend(
        EufySolixConnectivitySensor(coordinator, sn)
        for sn, dev in solix.items()
        if "connectivity" in dev.get("capabilities", [])
    )
    async_add_entities(entities)


class EufySdkBinarySensor(EufySdkPropertyEntity, BinarySensorEntity):
    """A read-only boolean property as a binary sensor."""

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        spec: dict[str, Any],
    ) -> None:
        """Infer a device_class from the property name."""
        super().__init__(coordinator, sn, spec)
        self._attr_device_class = _device_class(self._prop)

    @property
    def is_on(self) -> bool | None:
        """On when the property's live value is truthy."""
        v = self.prop_value
        return None if v is None else bool(v)


class EufyPushBinarySensor(EufySdkDeviceEntity, BinarySensorEntity):
    """A detection driven by push events: ON on the event, auto-OFF after a delay."""

    _attr_is_on = False

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        events: frozenset[str],
        spec: tuple[str, str, BinarySensorDeviceClass | None],
    ) -> None:
        """Bind to the push event(s) that flip this sensor on for this device."""
        super().__init__(coordinator, sn)
        key, name, device_class = spec
        self._events = events
        self._attr_unique_id = f"{sn}_{key}"
        self._attr_name = name
        self._attr_device_class = device_class
        self._cancel_off = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to the bus and cancel any pending auto-off on removal."""
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_TYPE, self._handle_event))
        self.async_on_remove(self._cancel_timer)

    @callback
    def _cancel_timer(self) -> None:
        """Cancel a pending auto-off timer, if any."""
        if self._cancel_off is not None:
            self._cancel_off()
            self._cancel_off = None

    @callback
    def _handle_event(self, event: Event) -> None:
        """Turn ON for this device's matching push event and (re)arm the auto-off."""
        data = event.data
        if data.get("deviceSn") != self._sn or data.get("event") not in self._events:
            return
        self._attr_is_on = True
        self._cancel_timer()
        self._cancel_off = async_call_later(
            self.hass, PUSH_AUTO_OFF_SECONDS, self._auto_off
        )
        self.async_write_ha_state()

    @callback
    def _auto_off(self, _now: object) -> None:
        """Clear the detection once the auto-off delay elapses."""
        self._cancel_off = None
        self._attr_is_on = False
        self.async_write_ha_state()


class EufyStreamingBinarySensor(EufySdkDeviceEntity, BinarySensorEntity):
    """ON while a live P2P feed is active (the camera is being streamed)."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "streaming"

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
    ) -> None:
        """Bind to a camera serial; start from the device-list value pre-event."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_streaming"
        self._active: bool | None = (
            None  # last streamState event; None → fall back to the poll
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to the bridge's streamState events for this device."""
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_TYPE, self._handle_event))

    @callback
    def _handle_event(self, event: Event) -> None:
        """Flip on/off from this device's streamState event (both edges)."""
        data = event.data
        if data.get("deviceSn") != self._sn or data.get("event") != "streamState":
            return
        self._active = bool(data.get("active"))
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Event value once seen; else the device-list `streaming` flag."""
        if self._active is not None:
            return self._active
        return bool(self.device.get("streaming"))


class EufyPackageBinarySensor(EufySdkDeviceEntity, BinarySensorEntity, RestoreEntity):
    """
    ON while a delivered package is waiting at the doorbell.

    Push is the only source: nothing on the device can be polled for it, so the last
    state is restored across restarts rather than dropped to off while a package
    still sits there.
    """

    _attr_translation_key = "package"
    _attr_icon = "mdi:package-variant-closed"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a doorbell serial; off until a delivery or a restored state."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_package"
        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        """Restore the last state, then follow this doorbell's package events."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_is_on = last.state == STATE_ON
        self.async_on_remove(self.hass.bus.async_listen(EVENT_TYPE, self._handle_event))

    @callback
    def _handle_event(self, event: Event) -> None:
        """Latch on at delivery or stranding, clear on pickup."""
        data = event.data
        if data.get("deviceSn") != self._sn:
            return
        name = data.get("event")
        if name in PACKAGE_PRESENT_EVENTS:
            self._attr_is_on = True
        elif name == PACKAGE_CLEARED_EVENT:
            self._attr_is_on = False
        else:
            return
        self.async_write_ha_state()


class EufyOnlineBinarySensor(EufySdkDeviceEntity, BinarySensorEntity):
    """
    Whether the device is online, from the cloud's `deviceStatus` (device-list poll).

    The eufy app shows such a device as offline, e.g. a battery camera that lost its
    radio link to the HomeBase. This sensor stays available while the device is
    offline, since that is the state it exists to show.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connectivity"
    _follows_device_online = False

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a device serial."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_online"

    @property
    def is_on(self) -> bool | None:
        """True while `deviceStatus` is True; None until the device reports it."""
        status = self.device.get("state", {}).get("deviceStatus")
        return None if status is None else bool(status)


class EufySolixConnectivitySensor(
    CoordinatorEntity[EufySdkDataUpdateCoordinator], BinarySensorEntity
):
    """
    Wi-Fi connectivity for an Anker Solix device (from the polled device snapshot).

    A CoordinatorEntity so it refreshes on the device-list poll (connectivity comes from
    the snapshot, not the telemetry event stream). Its HA device matches the Solix
    sensors (`solix:<sn>`).
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a Solix serial; build its Anker Solix device_info."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"solix_{sn}_connectivity"
        self._attr_translation_key = "connectivity"
        self._attr_device_info = solix_device_info(coordinator, sn)

    @property
    def is_on(self) -> bool:
        """True while the device reports Wi-Fi online."""
        return bool(self.coordinator.solix_devices.get(self._sn, {}).get("online"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The connected network + signal, so you can see WHICH Wi-Fi it's on."""
        dev = self.coordinator.solix_devices.get(self._sn, {})
        return {"ssid": dev.get("ssid"), "rssi": dev.get("rssi")}

    @property
    def available(self) -> bool:
        """Available while the bridge still lists this Solix device."""
        return super().available and self._sn in getattr(
            self.coordinator, "solix_devices", {}
        )
