"""The lock asks the device for its state when nothing else has reported it."""

# ruff: noqa: INP001, S101, SLF001

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.eufy_sdk.const import EVENT_TYPE
from custom_components.eufy_sdk.lock import (
    REFRESH_STATUS_MIN_INTERVAL_SECS,
    EufySdkLock,
)

LOCK = "T8530P0000000001"
TWO_READS = 2


class TestLockRefreshStatus(IsolatedAsyncioTestCase):
    """Exercise the refreshStatus reads around restarts, reports and commands."""

    async def asyncSetUp(self) -> None:
        """Bind a lock entity whose snapshot carries no lock state."""
        self.hass = HomeAssistant("/tmp/eufy-lock-refresh-test")  # noqa: S108
        self.coordinator = Mock(data={LOCK: {"state": {}}})
        self.coordinator.async_request_refresh = AsyncMock()
        self.client = Mock(action=AsyncMock(return_value=None))
        self.coordinator.config_entry.runtime_data.client = self.client
        self.entity = EufySdkLock(self.coordinator, LOCK)
        self.entity.hass = self.hass
        self.entity.async_write_ha_state = Mock()
        self.coordinator.async_update_listeners.side_effect = (
            self.entity._handle_coordinator_update
        )

    async def asyncTearDown(self) -> None:
        """Remove event subscriptions and pending action timers."""
        await self.entity.async_internal_will_remove_from_hass()
        await self.hass.async_stop()

    async def _add(self) -> None:
        with patch.object(CoordinatorEntity, "async_added_to_hass", new=AsyncMock()):
            await self.entity.async_added_to_hass()
        await self.hass.async_block_till_done()

    def _status_calls(self) -> int:
        calls = self.client.action.await_args_list
        return sum(1 for c in calls if c.args[1] == "refreshStatus")

    async def test_added_entity_reads_state_from_the_lock(self) -> None:
        """After a restart the lock's own answer replaces unknown."""
        self.client.action.return_value = True
        await self._add()
        self.client.action.assert_awaited_with(LOCK, "refreshStatus")
        assert self.entity.is_locked is True
        assert self.coordinator.data[LOCK]["state"]["locked"] is True

    async def test_no_settled_answer_leaves_state_unknown(self) -> None:
        """An undefined answer (bolt between positions) is not a state."""
        await self._add()
        assert self._status_calls() == 1
        assert self.entity.is_locked is None

    async def test_failed_read_leaves_state_and_allows_a_retry(self) -> None:
        """A P2P failure is logged and the next trigger asks again."""
        self.client.action.side_effect = TimeoutError("p2p timeout")
        await self._add()
        assert self.entity.is_locked is None
        self.client.action.side_effect = None
        self.client.action.return_value = False
        self.hass.bus.async_fire(EVENT_TYPE, {"event": "lockState", "deviceSn": LOCK})
        await self.hass.async_block_till_done()
        assert self.entity.is_locked is False

    async def test_undecoded_lock_report_asks_the_lock(self) -> None:
        """A lockState with no decoded boolean triggers a read."""
        await self._add()
        self.client.action.return_value = True
        self.hass.bus.async_fire(
            EVENT_TYPE, {"event": "lockState", "deviceSn": LOCK, "eventType": 999}
        )
        await self.hass.async_block_till_done()
        assert self._status_calls() == TWO_READS
        assert self.entity.is_locked is True

    async def test_decoded_report_needs_no_read(self) -> None:
        """A report that carries locked is applied directly."""
        await self._add()
        self.hass.bus.async_fire(
            EVENT_TYPE, {"event": "lockState", "deviceSn": LOCK, "locked": False}
        )
        await self.hass.async_block_till_done()
        assert self._status_calls() == 1
        assert self.entity.is_locked is False

    async def test_polls_while_unknown_are_rate_limited(self) -> None:
        """A poll re-asks only after the minimum interval."""
        await self._add()
        self.coordinator.async_update_listeners()
        await self.hass.async_block_till_done()
        assert self._status_calls() == 1
        self.entity._status_query_last -= REFRESH_STATUS_MIN_INTERVAL_SECS + 1
        self.client.action.return_value = True
        self.coordinator.async_update_listeners()
        await self.hass.async_block_till_done()
        assert self._status_calls() == TWO_READS
        assert self.entity.is_locked is True

    async def test_known_state_stops_poll_reads(self) -> None:
        """Once the state is known a poll does not wake the lock."""
        self.client.action.return_value = True
        await self._add()
        self.entity._status_query_last -= REFRESH_STATUS_MIN_INTERVAL_SECS + 1
        self.coordinator.async_update_listeners()
        await self.hass.async_block_till_done()
        assert self._status_calls() == 1

    async def test_reconcile_after_a_command_reads_the_lock(self) -> None:
        """The post-command reconcile settles on the lock's own answer."""
        await self._add()
        await self.entity.async_unlock()
        assert self.entity.is_locked is False
        self.client.action.return_value = True
        await self.entity._reconcile()
        await self.hass.async_block_till_done()
        assert self.entity.is_locked is True
