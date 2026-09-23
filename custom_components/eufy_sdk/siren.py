"""
Siren platform — the `siren` capability's dedicated trigger()/stop() actions.

Like `lock`, an alarm is not a writable bool: the SDK exposes it as momentary
methods, so this bypasses `switch.py`'s property routing and calls `client.action()`.

Which devices get one: every device whose bridge record lists the `siren` capability —
a HomeBase reporting hub-alarm params, a camera attached to one that reports the EAS
slot, or a standalone siren. The three do NOT share a command surface, and the split
runs right through turn_on:

  * HomeBase / attached camera — `trigger(seconds)` plus `stop`. No sounding state is
    reported, so the entity holds the written value until the duration runs out, the
    same optimistic pattern `lock.py` uses.
  * standalone siren — `test` plus `stop`; the SDK installs no `trigger` for it, and
    `test` takes no duration. Here the device reports `siren` (RING_STATUS), so the
    state is read rather than assumed — including alarms nobody in HA started.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from .entity import EufySdkDeviceEntity, has_capability

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry

# `siren.turn_on` may omit a duration, but the SDK rejects anything that is not a
# positive whole number of seconds — so a call without one gets this bounded default
# rather than a sound nobody asked to stop.
DEFAULT_DURATION_SECS = 30

# The alarm stops itself when the duration elapses, and no push reports that. Drop the
# optimistic hold a moment later, so a HomeBase that ran its own timeout still lands on
# the truth rather than a stuck "on".
OPTIMISTIC_GRACE_SECS = 2


def is_standalone_siren(record: dict | None) -> bool:
    """
    Whether this `siren` device is the standalone family (`test`/`stop`, no `trigger`).

    The codec is a sufficient discriminator because the capability's own detection
    already did the work: a sensor-codec device earns `siren` ONLY through the
    standalone evidence (reported alarm volume plus ring status or alarm timeout),
    while the other two families are a HomeBase (station codec) and a camera attached
    to one. So `codec == "sensor"` and "standalone" cannot come apart here.
    """
    return (record or {}).get("codec") == "sensor"


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a Siren entity per device that declares the `siren` capability."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EufySdkSiren(coordinator, sn)
        for sn, dev in coordinator.data.items()
        if has_capability(dev, "siren")
    )


class EufySdkSiren(EufySdkDeviceEntity, SirenEntity):
    """A eufy alarm output, driven by the SDK's `trigger`/`test`/`stop` actions."""

    _attr_name = "Siren"

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to an alarm-capable device serial and pin its command family."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_siren"
        self._expiry_unsub = None
        self._assumed_on: bool | None = None
        # The family is a property of the hardware, not of the moment — a device does
        # not change codec — so it is read once here rather than on every call.
        self._standalone = is_standalone_siren(coordinator.data.get(sn))
        # DURATION is advertised only where a duration wire exists. Claiming it on a
        # standalone siren would let HA accept a `duration` that `test` silently drops.
        self._attr_supported_features = (
            SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF
        )
        if not self._standalone:
            self._attr_supported_features |= SirenEntityFeature.DURATION
            # Only the standalone family reports whether it is sounding, so for a
            # HomeBase or an attached camera "off" is an assumption. Start from it
            # rather than from `None`: an alarm lasts its duration (30s unless asked
            # otherwise) and a restart takes longer, so "off" is nearly always true,
            # while "unknown" after every restart reads as a fault. `assumed_state`
            # declares the guess, and HA then offers separate on/off controls
            # instead of a toggle that claims to know.
            self._attr_assumed_state = True
            self._assumed_on = False

    @property
    def is_on(self) -> bool | None:
        """
        The sounding state a standalone siren reports, else the optimistic hold.

        `siren` is installed only for the standalone family, so for a HomeBase or an
        attached camera this is the written value until its duration elapses, and an
        assumed "off" before anything has been written (see `assumed_state`). A
        standalone siren that has not reported yet stays `None`: its real value is
        on the way, so there is nothing to assume.

        Where it IS reported, it is the device's own RING_STATUS: an alarm started from
        the eufy app, a keypad or a station rule shows up here too, not just ours.
        """
        reported = self.device.get("state", {}).get("siren")
        if reported is not None:
            return bool(reported)
        return self._assumed_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Sound the alarm — `trigger(duration)`, or `test` on a standalone siren."""
        client = self.client
        if self._standalone:
            # `test` is the standalone family's only "make noise now" wire, and it
            # carries no duration: the device stops itself after its own configured
            # alarm timeout. Nothing is assumed here — RING_STATUS reports the truth,
            # and a hold guessing at a duration the device owns would only fight it.
            await client.action(self._sn, "test")
            return
        duration = int(kwargs.get("duration") or DEFAULT_DURATION_SECS)
        await client.action(self._sn, "trigger", duration)
        self._hold(on=True)
        self._expiry_unsub = async_call_later(
            self.hass, duration + OPTIMISTIC_GRACE_SECS, self._expire
        )

    async def async_turn_off(self, **_: Any) -> None:
        """Silence a sounding alarm — `stop` is installed for every siren family."""
        client = self.client
        await client.action(self._sn, "stop")
        self._hold(on=False)

    @callback
    def _hold(self, *, on: bool) -> None:
        """Hold the just-written value and cancel any pending expiry."""
        if self._expiry_unsub is not None:
            self._expiry_unsub()
            self._expiry_unsub = None
        self._assumed_on = on
        self.async_write_ha_state()

    @callback
    def _expire(self, _now: Any) -> None:
        """
        Drop the hold once the duration has elapsed — the alarm stopped itself.

        `@callback` is load-bearing: `async_call_later` runs an undecorated function in
        an executor thread, and `async_write_ha_state` must not be called from one.
        """
        self._expiry_unsub = None
        self._assumed_on = False
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a pending expiry when the entity goes away."""
        if self._expiry_unsub is not None:
            self._expiry_unsub()
            self._expiry_unsub = None
        await super().async_will_remove_from_hass()
