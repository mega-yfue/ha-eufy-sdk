# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, SLF001

import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import custom_components.eufy_sdk as integration
from custom_components.eufy_sdk.api import EufySdkApiClient
from custom_components.eufy_sdk.const import CONF_HOST, CONF_PORT


class StartupDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_during_first_refresh_and_after_initialization(self):
        hass = Mock()
        hass.bus.async_fire = Mock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()

        entry = Mock()
        entry.data = {CONF_HOST: "bridge", CONF_PORT: 3000}
        entry.options = {}
        entry.domain = "eufy_sdk"
        entry.entry_id = "test-entry"
        entry.unique_id = "synthetic-bridge-id"
        entry.add_update_listener = Mock(return_value=lambda: None)
        entry.async_on_unload = Mock()
        scheduled = []
        entry.async_create_background_task = Mock(
            side_effect=lambda _hass, coroutine, _name: scheduled.append(coroutine)
        )

        coordinator = Mock(data={"homebase": {"state": {"armingMode": 1}}})
        coordinator.async_config_entry_first_refresh = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()

        client = Mock(bridge_id="synthetic-bridge-id")
        client.set_poll_ms = AsyncMock()
        client.get_properties = AsyncMock(return_value=[])
        callbacks = []

        def make_client(**kwargs: Any) -> Mock:
            callbacks.append(kwargs["on_event"])
            return client

        async def first_refresh() -> None:
            coordinator.data = None
            receiver = EufySdkApiClient(
                host="bridge", port=3000, session=Mock(), on_event=callbacks[0]
            )
            for event in (
                {"event": "armingModeChanged", "deviceSn": "homebase"},
                {"event": "armingModeChanged", "deviceSn": "homebase", "mode": 1},
                {"event": "contactState", "deviceSn": "homebase", "open": True},
                {
                    "event": "propertyChanged",
                    "deviceSn": "homebase",
                    "property": "armingMode",
                    "value": 1,
                },
                {"event": "ready"},
            ):
                receiver._dispatch_event(event)
                hass.bus.async_fire.assert_called_with(integration.EVENT_TYPE, event)
            self.assertEqual(len(scheduled), 1)
            await scheduled.pop()
            coordinator.async_request_refresh.assert_awaited_once_with()
            coordinator.async_request_refresh.reset_mock()
            coordinator.data = {
                "homebase": {
                    "name": "Synthetic station",
                    "model": "T8N00",
                    "category": "station",
                    "state": {"armingMode": 1},
                }
            }

        hass.async_create_task = Mock(side_effect=scheduled.append)
        coordinator.async_config_entry_first_refresh.side_effect = first_refresh

        with (
            patch.object(
                integration, "EufySdkDataUpdateCoordinator", return_value=coordinator
            ),
            patch.object(integration, "EufySdkApiClient", side_effect=make_client),
            patch.object(integration, "async_get_clientsession", return_value=Mock()),
            patch.object(
                integration, "async_get_loaded_integration", return_value=Mock()
            ),
        ):
            await integration.async_setup_entry(hass, entry)
            self.assertEqual(len(callbacks), 1)

        callbacks[0]({"event": "armingModeChanged", "deviceSn": "homebase"})
        self.assertEqual(len(scheduled), 1)
        await scheduled.pop()
        coordinator.async_request_refresh.assert_awaited_once_with()

        callbacks[0]({"event": "armingModeChanged", "deviceSn": "unknown"})
        self.assertEqual(scheduled, [])


if __name__ == "__main__":
    unittest.main()
