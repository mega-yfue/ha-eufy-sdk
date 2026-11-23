"""
Lock platform — the `lock` capability's dedicated lock()/unlock() actions.

Unlike a writable bool property, a eufy lock has no reachable `device.set` path
(see `locked` in the SDK: `writtenElsewhere` points at these two actions, not a
sibling property) — so this bypasses `switch.py`'s generic property routing
entirely and calls `client.action()` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later

from .const import EVENT_TYPE, LOGGER
from .entity import EufySdkDeviceEntity, has_capability

LOCK_OWNED_PROPS = frozenset({"locked"})

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry

# The `locked` param is `guessed`/unreliable per the SDK (no stable state param —
# locks report via push events instead), so an optimistic hold after a write is
# reconciled the same way EufySdkPropertyEntity does for properties.
POST_ACTION_REFRESH_SECS = 20

# A video smart lock answers a live status query (`refreshStatus`, a read-only P2P
# round-trip). It is asked when the entity is added, after a `lockState` report with
# no decoded `locked`, and after a command. While the state stays unknown, a poll asks
# again at most this often, so a sleeping battery lock is not woken on every refresh.
REFRESH_STATUS_MIN_INTERVAL_SECS = 600


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a Lock entity per device that declares the `lock` capability."""
    coordinator = entry.runtime_data.coordinator
    entities = [
        EufySdkLock(coordinator, sn)
        for sn, dev in coordinator.data.items()
        if has_capability(dev, "lock")
    ]
    async_add_entities(entities)


class EufySdkLock(EufySdkDeviceEntity, LockEntity):
    """A eufy smart lock, driven by the SDK's `lock`/`unlock` actions."""

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, sn: str) -> None:
        """Bind to a lock-capable device serial."""
        super().__init__(coordinator, sn)
        self._attr_unique_id = f"{sn}_lock"
        self._post_action_unsub = None
        self._assumed_locked: bool | None = None
        self._reported_locked: bool | None = None
        self._status_query_running = False
        self._status_query_last: float | None = None

    @property
    def is_locked(self) -> bool | None:
        """The optimistic state, else snapshot state with a reported-state fallback."""
        if self._assumed_locked is not None:
            return self._assumed_locked
        v = self.device.get("state", {}).get("locked")
        return self._reported_locked if v is None else bool(v)

    async def async_added_to_hass(self) -> None:
        """Subscribe to the bridge's decoded lock-state reports."""
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_TYPE, self._handle_event))
        # Lock state arrives only on push reports, so after a restart nothing says
        # locked or unlocked until the lock next moves. Ask the lock now.
        self._schedule_status_query()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-render, and ask the lock again if its state is still unknown."""
        super()._handle_coordinator_update()
        if self.is_locked is None:
            self._schedule_status_query(rate_limited=True)

    @callback
    def _schedule_status_query(self, *, rate_limited: bool = False) -> None:
        """Start one status query unless one is running or ran too recently."""
        if self._status_query_running or self.hass is None:
            return
        now = self.hass.loop.time()
        if (
            rate_limited
            and self._status_query_last is not None
            and now - self._status_query_last < REFRESH_STATUS_MIN_INTERVAL_SECS
        ):
            return
        self._status_query_running = True
        self._status_query_last = now
        self.hass.async_create_task(self._query_status())

    async def _query_status(self) -> None:
        """Read the lock's own state; keep the current value if it gives none."""
        try:
            locked = await self.client.action(self._sn, "refreshStatus")
        except Exception as err:  # noqa: BLE001 - a failed read leaves state as is
            LOGGER.debug("lock %s: refreshStatus failed: %s", self._sn, err)
            return
        finally:
            self._status_query_running = False
        if not isinstance(locked, bool):
            return
        self._apply_reported(locked=locked)

    @callback
    def _apply_reported(self, *, locked: bool) -> None:
        """Record a device-reported state and release any optimistic hold."""
        self._reported_locked = locked
        self.device.setdefault("state", {})["locked"] = locked
        self._assumed_locked = None
        self.coordinator.async_update_listeners()

    @callback
    def _handle_event(self, event: Event) -> None:
        """Apply a reported lock transition and release any optimistic hold."""
        data = event.data
        if data.get("event") != "lockState" or data.get("deviceSn") != self._sn:
            return
        locked = data.get("locked")
        if not isinstance(locked, bool):
            # A lock event the SDK does not decode to locked/unlocked (an alarm, an
            # unmapped code): the lock may still have moved, so ask it.
            self._schedule_status_query()
            return
        self._apply_reported(locked=locked)

    async def async_lock(self, **_: Any) -> None:
        """Lock the deadbolt/door."""
        await self._actuate(locked=True)

    async def async_unlock(self, **_: Any) -> None:
        """Unlock the deadbolt/door."""
        await self._actuate(locked=False)

    async def _actuate(self, *, locked: bool) -> None:
        """Call the dedicated action, hold the value optimistically, reconcile later."""
        client = self.client
        await client.action(self._sn, "lock" if locked else "unlock")
        self._assumed_locked = locked
        self.async_write_ha_state()
        if self._post_action_unsub is not None:
            self._post_action_unsub()
        self._post_action_unsub = async_call_later(
            self.hass, POST_ACTION_REFRESH_SECS, self._post_action_refresh
        )

    @callback
    def _post_action_refresh(self, _now: Any) -> None:
        """Fire the delayed post-action reconcile."""
        self._post_action_unsub = None
        self.hass.async_create_task(self._reconcile())

    async def _reconcile(self) -> None:
        """Pull fresh state, drop the optimistic hold, re-render to truth."""
        await self.coordinator.async_request_refresh()
        self._assumed_locked = None
        self.async_write_ha_state()
        self._schedule_status_query()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a pending delayed refresh when the entity goes away."""
        if self._post_action_unsub is not None:
            self._post_action_unsub()
            self._post_action_unsub = None
        await super().async_will_remove_from_hass()
