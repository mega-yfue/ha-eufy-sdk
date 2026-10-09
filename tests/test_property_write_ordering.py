"""Regression tests for optimistic property-write ordering."""

# ruff: noqa: FBT003, INP001, S101, SLF001

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.eufy_sdk.entity import EufySdkPropertyEntity
from custom_components.eufy_sdk.property_sync import apply_property_changed_event

LATEST_OWNER = 2


class TestPropertyWriteOrdering(IsolatedAsyncioTestCase):
    """Verify ownership between RPC writes and authoritative reports."""

    def _entity(
        self,
        *,
        value: bool | None = False,
        write_only: bool = False,
    ) -> tuple[EufySdkPropertyEntity, Mock, Mock]:
        client = Mock()

        coordinator = Mock()
        coordinator.data = {"camera": {"state": {"enabled": value}}}
        coordinator.config_entry.runtime_data.client = client
        coordinator.async_request_refresh = AsyncMock()

        entity = EufySdkPropertyEntity.__new__(EufySdkPropertyEntity)
        entity.coordinator = coordinator
        entity._sn = "camera"
        entity._prop = "enabled"
        entity._spec = {"name": "enabled", "writeOnly": write_only}
        entity._post_write_unsub = None
        entity._post_write_owner = None
        entity._assumed_value = None
        entity._optimistic_owner = None
        entity._write_generation = 0
        entity._report_generation = 0
        entity._removed = False
        entity.hass = Mock()
        entity.async_write_ha_state = Mock()

        return entity, coordinator, client

    @staticmethod
    def _report(
        entity: EufySdkPropertyEntity,
        coordinator: Mock,
        *,
        value: bool,
    ) -> None:
        data = {
            "event": "propertyChanged",
            "deviceSn": "camera",
            "property": "enabled",
            "value": value,
        }
        apply_property_changed_event(coordinator, data)
        entity._handle_property_changed(SimpleNamespace(data=data))

    def test_property_event_filter_rejects_unrelated_events(self) -> None:
        """Only matching valued property reports reach the entity callback."""
        entity, _coordinator, _client = self._entity()

        def event(**data: object) -> SimpleNamespace:
            return SimpleNamespace(data=data)

        assert entity._property_event_filter(
            event(
                event="propertyChanged",
                deviceSn="camera",
                property="enabled",
                value=True,
            )
        )
        assert not entity._property_event_filter(
            event(
                event="solixReading",
                deviceSn="camera",
                property="enabled",
                value=True,
            )
        )
        assert not entity._property_event_filter(
            event(
                event="propertyChanged",
                deviceSn="other",
                property="enabled",
                value=True,
            )
        )
        assert not entity._property_event_filter(
            event(
                event="propertyChanged",
                deviceSn="camera",
                property="statusLed",
                value=True,
            )
        )
        assert not entity._property_event_filter(
            event(
                event="propertyChanged",
                deviceSn="camera",
                property="enabled",
            )
        )

    async def test_report_during_pending_write_is_not_overwritten(self) -> None:
        """A report received during RPC remains visible after RPC completion."""
        entity, coordinator, client = self._entity(value=True)
        gate = asyncio.Event()

        async def set_property(*_args: object) -> None:
            await gate.wait()

        client.set_property = AsyncMock(side_effect=set_property)

        with patch(
            "custom_components.eufy_sdk.entity.async_call_later",
            return_value=Mock(),
        ) as call_later:
            task = asyncio.create_task(entity.write(False))
            await asyncio.sleep(0)

            self._report(entity, coordinator, value=True)
            assert entity.prop_value is True

            gate.set()
            await task

            assert entity._assumed_value is None
            assert entity.prop_value is True
            call_later.assert_called_once()

    async def test_report_after_rpc_releases_optimistic_hold(self) -> None:
        """A report after RPC completion releases optimistic state immediately."""
        entity, coordinator, client = self._entity(value=False)
        client.set_property = AsyncMock()
        cancel = Mock()

        with patch(
            "custom_components.eufy_sdk.entity.async_call_later",
            return_value=cancel,
        ):
            await entity.write(True)

            assert entity.prop_value is True
            assert entity._optimistic_owner == 1

            self._report(entity, coordinator, value=False)

            assert entity._assumed_value is None
            assert entity._optimistic_owner is None
            assert entity.prop_value is False
            cancel.assert_called_once()

    async def test_overlapping_writes_keep_latest_owner(self) -> None:
        """An older RPC completing last cannot overwrite a newer write."""
        entity, _coordinator, client = self._entity(value=False)
        first = asyncio.Event()
        second = asyncio.Event()

        async def set_property(_sn: str, _prop: str, value: object) -> None:
            await (first if value else second).wait()

        client.set_property = AsyncMock(side_effect=set_property)

        with patch(
            "custom_components.eufy_sdk.entity.async_call_later",
            return_value=Mock(),
        ) as call_later:
            older = asyncio.create_task(entity.write(True))
            await asyncio.sleep(0)

            newer = asyncio.create_task(entity.write(False))
            await asyncio.sleep(0)

            second.set()
            await newer

            assert entity.prop_value is False
            assert entity._optimistic_owner == LATEST_OWNER

            first.set()
            await older

            assert entity.prop_value is False
            assert entity._optimistic_owner == LATEST_OWNER
            assert call_later.call_count == 1

    async def test_write_only_setting_keeps_successful_value(self) -> None:
        """Write-only settings keep their last successful optimistic value."""
        entity, _coordinator, client = self._entity(
            value=None,
            write_only=True,
        )
        client.set_property = AsyncMock()

        with patch("custom_components.eufy_sdk.entity.async_call_later") as call_later:
            await entity.write(True)

            assert entity.prop_value is True
            assert entity._optimistic_owner == 1
            call_later.assert_not_called()

    async def test_cancelled_write_never_installs_optimistic_state(self) -> None:
        """Cancellation during RPC leaves no optimistic value behind."""
        entity, _coordinator, client = self._entity(value=False)
        gate = asyncio.Event()

        async def set_property(*_args: object) -> None:
            await gate.wait()

        client.set_property = AsyncMock(side_effect=set_property)

        task = asyncio.create_task(entity.write(True))
        await asyncio.sleep(0)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert entity._assumed_value is None
        assert entity.prop_value is False

    async def test_readded_entity_accepts_writes_after_removal(self) -> None:
        """Re-adding the same entity instance restores write ownership."""
        entity, _coordinator, client = self._entity(
            value=None,
            write_only=True,
        )
        client.set_property = AsyncMock()

        entity.async_on_remove = Mock()
        entity.hass.bus.async_listen = Mock(return_value=Mock())

        with (
            patch.object(
                CoordinatorEntity,
                "async_will_remove_from_hass",
                new=AsyncMock(),
            ),
            patch.object(
                CoordinatorEntity,
                "async_added_to_hass",
                new=AsyncMock(),
            ),
        ):
            await entity.async_will_remove_from_hass()
            assert entity._removed is True

            await entity.async_added_to_hass()
            assert entity._removed is False

            await entity.write(True)

        assert entity.prop_value is True
        assert entity._optimistic_owner == entity._write_generation
        client.set_property.assert_awaited_once_with(
            "camera",
            "enabled",
            True,
        )

    async def test_entity_removal_invalidates_pending_write(self) -> None:
        """A write completing after removal cannot regain optimistic ownership."""
        entity, _coordinator, client = self._entity(value=False)
        gate = asyncio.Event()

        async def set_property(*_args: object) -> None:
            await gate.wait()

        client.set_property = AsyncMock(side_effect=set_property)

        task = asyncio.create_task(entity.write(True))
        await asyncio.sleep(0)

        with patch.object(
            CoordinatorEntity,
            "async_will_remove_from_hass",
            new=AsyncMock(),
        ):
            await entity.async_will_remove_from_hass()

        gate.set()
        await task

        assert entity._assumed_value is None
        assert entity._removed is True
        assert entity.prop_value is False
