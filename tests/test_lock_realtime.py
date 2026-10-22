"""Decoded lock reports update the entity and supersede optimistic state."""

# ruff: noqa: INP001, S101, SLF001

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.eufy_sdk.const import EVENT_TYPE
from custom_components.eufy_sdk.lock import EufySdkLock


class TestLockRealtime(IsolatedAsyncioTestCase):
    """Exercise the lock's event subscription without changing command policy."""

    async def asyncSetUp(self) -> None:
        """Bind a lock entity with an initially unknown snapshot."""
        self.hass = HomeAssistant("/tmp/eufy-lock-test")  # noqa: S108
        self.coordinator = Mock(data={"lock": {"state": {}}})
        self.client = Mock(action=AsyncMock())
        self.coordinator.config_entry.runtime_data.client = self.client
        self.entity = EufySdkLock(self.coordinator, "lock")
        self.entity.hass = self.hass
        self.entity.async_write_ha_state = Mock()
        self.coordinator.async_update_listeners.side_effect = (
            self.entity._handle_coordinator_update
        )
        with patch.object(CoordinatorEntity, "async_added_to_hass", new=AsyncMock()):
            await self.entity.async_added_to_hass()

    async def asyncTearDown(self) -> None:
        """Remove event subscriptions and pending action timers."""
        await self.entity.async_internal_will_remove_from_hass()
        await self.hass.async_stop()

    async def _report(self, *, locked: bool) -> None:
        """Deliver the same decoded boolean shape observed on the live bridge."""
        self.hass.bus.async_fire(
            EVENT_TYPE, {"event": "lockState", "deviceSn": "lock", "locked": locked}
        )
        await self.hass.async_block_till_done()

    async def test_manual_reports_update_lock_and_unlock_state(self) -> None:
        """Manual transitions update coordinator state and the lock entity."""
        for locked in (True, False):
            self.entity.async_write_ha_state.reset_mock()
            await self._report(locked=locked)
            assert self.entity.is_locked is locked
            assert self.coordinator.data["lock"]["state"]["locked"] is locked
            self.entity.async_write_ha_state.assert_called_once_with()

    async def test_report_supersedes_the_existing_optimistic_hold(self) -> None:
        """Keep optimistic actions, but let a real report correct their state."""
        for locked in (True, False):
            if locked:
                await self.entity.async_lock()
            else:
                await self.entity.async_unlock()
            assert self.entity.is_locked is locked
            await self._report(locked=not locked)
            assert self.entity.is_locked is not locked

    async def test_report_survives_refresh_without_lock_state(self) -> None:
        """Keep reports when snapshot state is absent; prefer a supplied value."""
        assert self.entity.is_locked is None
        for locked in (True, False):
            await self._report(locked=locked)
            for state in ({}, {"locked": None}):
                self.coordinator.data = {"lock": {"state": state}}
                self.entity.async_write_ha_state.reset_mock()
                self.coordinator.async_update_listeners()
                assert self.entity.is_locked is locked
                self.entity.async_write_ha_state.assert_called_once_with()
            self.coordinator.data = {"lock": {"state": {"locked": not locked}}}
            self.coordinator.async_update_listeners()
            assert self.entity.is_locked is not locked
