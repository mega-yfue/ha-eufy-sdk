"""
The eufy_sdk integration — talks to a ha-eufy-sdk bridge over WebSocket.

https://github.com/mega-yfue/ha-eufy-sdk
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.loader import async_get_loaded_integration

from .alarm_logic import PHASE_STOPPED
from .alarm_sync import (
    alarm_station_serial,
    apply_alarm_event,
    clear_alarm,
    end_cancelled_delay,
)
from .api import EufySdkApiClient
from .arming_sync import apply_arming_mode_event
from .const import (
    ALARM_AUTO_CLEAR_SECONDS,
    CONF_HOST,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    DEFAULT_POLL_INTERVAL_MIN,
    DOMAIN,
    EVENT_TYPE,
    LOGGER,
)
from .coordinator import EufySdkDataUpdateCoordinator
from .data import EufySdkData
from .property_sync import apply_property_changed_event

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import CALLBACK_TYPE, HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry

    from .data import EufySdkConfigEntry

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.CAMERA,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.EVENT,
    Platform.LIGHT,
    Platform.LOCK,
    Platform.ALARM_CONTROL_PANEL,
    Platform.SIREN,
]


def _alarm_lifecycle(
    hass: HomeAssistant,
    entry: EufySdkConfigEntry,
    coordinator: EufySdkDataUpdateCoordinator,
) -> Callable[[dict], None]:
    """
    Build the handler that folds a station's `alarm` pushes into its live state.

    The hub pushes a stop when it silences its alarm from the app, the keypad or
    itself, but NOT when a triggered duration simply runs out — so every start also
    arms a fallback that clears the flags after ALARM_AUTO_CLEAR_SECONDS unless a
    stop (or a new start, which re-arms it) gets there first.
    """
    timers: dict[str, CALLBACK_TYPE] = {}

    def on_alarm(evt: dict) -> None:
        phase = apply_alarm_event(coordinator, entry.runtime_data.station_alarms, evt)
        serial = alarm_station_serial(coordinator, evt)
        if phase is None or not serial:
            return
        if (cancel := timers.pop(serial, None)) is not None:
            cancel()
        if phase == PHASE_STOPPED:
            return

        @callback
        def _auto_clear(_now: datetime, sn: str = serial) -> None:
            timers.pop(sn, None)
            clear_alarm(coordinator, entry.runtime_data.station_alarms, sn)

        timers[serial] = async_call_later(hass, ALARM_AUTO_CLEAR_SECONDS, _auto_clear)

    def _cancel_all() -> None:
        for cancel in timers.values():
            cancel()
        timers.clear()

    entry.async_on_unload(_cancel_all)
    return on_alarm


async def async_setup_entry(hass: HomeAssistant, entry: EufySdkConfigEntry) -> bool:
    """Set up eufy_sdk from a config entry."""
    poll_min = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL_MIN)
    coordinator = EufySdkDataUpdateCoordinator(
        hass=hass,
        logger=LOGGER,
        name=DOMAIN,
        # HA reads the bridge at the same cadence the bridge polls the cloud.
        update_interval=timedelta(minutes=poll_min),
        config_entry=entry,
    )

    # Forward every bridge event onto the HA event bus for automations — and recover
    # fast when the bridge comes back. A bridge restart drops the WS and fails one
    # coordinator poll, marking every entity `unavailable`; without a nudge they stay
    # that way (and detections don't show) until the next poll, up to `poll_min` minutes
    # later. So refresh the coordinator immediately on the bridge's `ready` broadcast
    # (sent on every boot) and on a WS reconnect.
    def _refresh_now() -> None:
        hass.async_create_task(coordinator.async_request_refresh())

    on_alarm = _alarm_lifecycle(hass, entry, coordinator)

    # Disarming (or going Home) during an entry/exit countdown answers with a mode
    # change, not an alarm stop — so a mode change to one of those ends `pending`.
    def _end_cancelled_delay(serial: str | None) -> None:
        if serial:
            end_cancelled_delay(coordinator, entry.runtime_data.station_alarms, serial)

    async def _refresh_then_end_cancelled_delay(serial: str) -> None:
        await coordinator.async_request_refresh()
        _end_cancelled_delay(serial)

    def _on_event(evt: dict) -> None:
        event = evt.get("event")

        # Generic device-state changes arrive already decoded by the SDK. Apply
        # valued changes directly so switches/selects/sensors update immediately;
        # a valueless change only tells us to re-read that device's state.
        if event == "propertyChanged":
            result = apply_property_changed_event(coordinator, evt)
            if result == "refresh":
                serial = evt.get("deviceSn") or evt.get("sn")
                if serial and serial in coordinator.data:
                    entry.async_create_background_task(
                        hass,
                        coordinator.async_request_refresh(),
                        f"{evt.get('property', 'property')} refresh",
                    )

        # Fire after applying direct state so entity listeners reacting to the bus
        # already see the new coordinator value.
        hass.bus.async_fire(EVENT_TYPE, evt)

        if event == "contactState":
            sn = evt.get("deviceSn") or evt.get("sn")
            if sn and sn in coordinator.data and "open" in evt:
                coordinator.data[sn].setdefault("state", {})["contact"] = bool(
                    evt.get("open")
                )
                coordinator.async_update_listeners()
        elif event == "armingModeChanged":
            serial = evt.get("deviceSn") or evt.get("sn")
            if "mode" in evt:
                apply_arming_mode_event(coordinator, evt)
                _end_cancelled_delay(serial)
            elif serial and serial in coordinator.data:
                entry.async_create_background_task(
                    hass,
                    _refresh_then_end_cancelled_delay(serial),
                    "arming mode refresh",
                )
        elif event == "alarm":
            on_alarm(evt)
        elif event == "ready":
            _refresh_now()

    client = EufySdkApiClient(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        session=async_get_clientsession(hass),
        on_event=_on_event,
        on_reconnect=_refresh_now,
    )
    entry.runtime_data = EufySdkData(
        client=client,
        integration=async_get_loaded_integration(hass, entry.domain),
        coordinator=coordinator,
    )

    await coordinator.async_config_entry_first_refresh()

    # Push the chosen poll interval to the bridge (the cloud-poll cadence lives there).
    try:
        await client.set_poll_ms(poll_min * 60_000)
    except Exception as err:  # noqa: BLE001 - a failed config push shouldn't block setup
        LOGGER.warning("could not set bridge poll interval: %s", err)

    # Property manifests are static per device — fetch once so the platforms can
    # build switch/select/number/sensor entities. A device that fails is skipped.
    properties: dict[str, list] = {}
    for sn, dev in coordinator.data.items():
        if dev.get("error"):
            continue
        try:
            properties[sn] = await client.get_properties(sn)
        except Exception as err:  # noqa: BLE001 - one bad device must not abort setup
            LOGGER.warning("could not fetch properties for %s: %s", sn, err)
    entry.runtime_data.properties = properties

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """
    Allow deleting a device from the UI once the bridge no longer reports it.

    A camera or HomeBase removed from (or unshared with) the eufy account stays in
    the device registry with nothing behind it, and without this hook it could never
    be deleted. A device the bridge still lists is refused: it would only come back
    on the next poll. Solix devices (`solix:<sn>`) are checked against the Solix list.

    Nothing is deleted while the last poll failed: the device list is then empty or
    stale, and every device would read as "no longer reported".
    """
    coordinator = entry.runtime_data.coordinator
    if not coordinator.last_update_success:
        return False
    eufy = coordinator.data or {}
    solix = coordinator.solix_devices or {}
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if identifier.startswith("solix:"):
            if identifier.removeprefix("solix:") in solix:
                return False
        elif identifier in eufy:
            return False
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EufySdkConfigEntry) -> bool:
    """Unload a config entry and close the bridge connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.close()
    return unloaded
