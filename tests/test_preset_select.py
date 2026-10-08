# ruff: noqa: ANN201, D100, D102, INP001, PT009, SLF001

import asyncio
import unittest
from collections.abc import Coroutine
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.eufy_sdk.select import EufySdkPresetSelect

SN = "T8410P0000000001"


async def _never_answers(_sn: str) -> None:
    await asyncio.Event().wait()


class PresetSelectReadTest(unittest.IsolatedAsyncioTestCase):
    """The slot read is P2P and can hang on a sleeping camera; nothing may await it."""

    def setUp(self):
        self.tasks: list[asyncio.Task] = []
        coord = Mock()
        coord.data = {SN: {"name": "Garden", "streaming": True}}
        runtime = coord.config_entry.runtime_data
        runtime.preset_slots = {}
        runtime.selected_preset = {}
        # A sleeping battery camera: the read never answers.
        runtime.client.preset_slots = AsyncMock(side_effect=_never_answers)
        coord.config_entry.async_create_background_task = Mock(
            side_effect=lambda _hass, coro, _name: self._track(coro)
        )
        self.entity = EufySdkPresetSelect(coord, SN)
        self.entity.hass = Mock()
        self.entity.async_get_last_state = AsyncMock(return_value=None)
        self.entity.async_on_remove = Mock()
        self.entity.async_write_ha_state = Mock()

    def _track(self, coro: Coroutine) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def test_added_to_hass_returns_while_the_read_hangs(self):
        with patch.object(CoordinatorEntity, "async_added_to_hass", AsyncMock()):
            await asyncio.wait_for(self.entity.async_added_to_hass(), timeout=1)

        self.assertEqual(len(self.tasks), 1)
        self.assertFalse(self.tasks[0].done())
        # Removing the entity cancels the in-flight read.
        self.entity.async_on_remove.assert_called_once_with(self.tasks[0].cancel)

    async def test_coordinator_update_reads_in_a_background_task(self):
        self.entity._handle_coordinator_update()

        self.assertEqual(len(self.tasks), 1)
        self.entity.hass.async_create_task.assert_not_called()
