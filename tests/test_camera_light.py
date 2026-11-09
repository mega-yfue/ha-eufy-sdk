"""Camera lights use the property write path and keep automatic lighting separate."""

# ruff: noqa: FBT003, INP001, S101, SLF001

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.light import ColorMode
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from custom_components.eufy_sdk import light, switch
from custom_components.eufy_sdk.property_sync import apply_property_changed_event

LIGHT = {"name": "light", "type": "bool", "writable": True}
AUTO_LIGHT = {"name": "lightSettingsEnable", "type": "bool", "writable": True}


class TestCameraLight(IsolatedAsyncioTestCase):
    """Exercise camera light discovery, state, and writes without hardware."""

    async def asyncSetUp(self) -> None:
        """Set up a camera that exposes both manual and automatic light controls."""
        self.coordinator = Mock(
            data={"camera": {"capabilities": ["camera"], "state": {}}},
            solix_devices={},
        )
        self.client = Mock(set_property=AsyncMock(), list_effects=AsyncMock())
        self.entry = SimpleNamespace(
            runtime_data=SimpleNamespace(
                coordinator=self.coordinator,
                properties={"camera": [LIGHT, AUTO_LIGHT]},
                client=self.client,
            )
        )
        self.coordinator.config_entry = self.entry

    def _entity(self) -> light.EufySdkCameraLight:
        entity = light.EufySdkCameraLight(self.coordinator, "camera", LIGHT)
        entity.hass = Mock()
        entity.async_write_ha_state = Mock()
        return entity

    async def test_setup_exposes_manual_light_as_a_primary_onoff_control(self) -> None:
        """Camera-only setups add a light without fetching smart-light effects."""
        for spec in (LIGHT, {**LIGHT, "reported": False}):
            with self.subTest(spec=spec):
                self.entry.runtime_data.properties["camera"] = [spec, AUTO_LIGHT]
                added = []
                await light.async_setup_entry(Mock(), self.entry, added.extend)
                assert len(added) == 1
                entity = added[0]
                assert isinstance(entity, light.EufySdkCameraLight)
                assert entity.unique_id == "camera_light"
                assert entity.name == "Light"
                assert entity.entity_category is None
                assert entity.color_mode == ColorMode.ONOFF
                assert entity.supported_color_modes == {ColorMode.ONOFF}
        self.client.list_effects.assert_not_awaited()

    async def test_setup_skips_properties_that_are_not_readable_onoff_lights(
        self,
    ) -> None:
        """Other settings and unsupported light specs do not produce lights."""
        for spec in (
            AUTO_LIGHT,
            {**LIGHT, "writable": False},
            {**LIGHT, "type": "number"},
            {**LIGHT, "writeOnly": True},
        ):
            with self.subTest(spec=spec):
                self.entry.runtime_data.properties["camera"] = [spec]
                added = []
                await light.async_setup_entry(Mock(), self.entry, added.extend)
                assert not added

    async def test_switch_is_preserved_and_automatic_lighting_stays_separate(
        self,
    ) -> None:
        """Keep the manual switch enabled but hidden, and settings visible."""
        added = []
        await switch.async_setup_entry(Mock(), self.entry, added.extend)
        switches = {entity._prop: entity for entity in added}
        manual = switches["light"]
        assert manual.unique_id == "camera_light"
        assert manual.entity_registry_enabled_default
        assert not manual.entity_registry_visible_default
        assert manual.entity_category == EntityCategory.CONFIG
        automatic = switches["lightSettingsEnable"]
        assert automatic.entity_registry_visible_default
        assert automatic.entity_category == EntityCategory.CONFIG
        automatic.write = AsyncMock()
        await automatic.async_turn_on()
        automatic.write.assert_awaited_once_with(value=True)

    async def test_smart_lights_keep_their_rgb_entity_without_duplicate_ids(
        self,
    ) -> None:
        """A mixed fleet preserves RGB/effects even if a smart light lists `light`."""
        self.coordinator.data["smart"] = {
            "capabilities": ["smart_light"],
            "state": {},
        }
        self.entry.runtime_data.properties["smart"] = [LIGHT]
        self.client.list_effects.return_value = [{"id": 1, "name": "Aurora"}]
        added = []
        await light.async_setup_entry(Mock(), self.entry, added.extend)
        assert len(added) == 2  # noqa: PLR2004
        entities = {entity.unique_id: entity for entity in added}
        assert isinstance(entities["camera_light"], light.EufySdkCameraLight)
        smart = entities["smart_light"]
        assert isinstance(smart, light.EufySdkSmartLight)
        assert smart.supported_color_modes == {ColorMode.RGB}
        assert smart.effect_list == ["Aurora"]
        self.client.list_effects.assert_awaited_once_with()

    def test_state_is_unknown_until_reported(self) -> None:
        """A missing report stays unknown; false is a valid off state."""
        entity = self._entity()
        assert entity.is_on is None
        for value in (False, True):
            self.coordinator.data["camera"]["state"]["light"] = value
            assert entity.is_on is value

    async def test_commands_use_property_writes_and_reconciliation(self) -> None:
        """Light services write the manual property and reuse optimistic state."""
        entity = self._entity()
        with patch("custom_components.eufy_sdk.entity.async_call_later") as later:
            await entity.async_turn_on()
            self.client.set_property.assert_awaited_once_with("camera", "light", True)
            assert entity.is_on is True
            later.assert_called_once()

            await entity.async_turn_off()
            self.client.set_property.assert_awaited_with("camera", "light", False)
            assert entity.is_on is False

    async def test_failed_command_preserves_reported_state(self) -> None:
        """A rejected bridge write does not claim the light was turned on."""
        entity = self._entity()
        self.coordinator.data["camera"]["state"]["light"] = False
        self.client.set_property.side_effect = HomeAssistantError("write rejected")
        with (
            patch("custom_components.eufy_sdk.entity.async_call_later") as later,
            pytest.raises(HomeAssistantError, match="write rejected"),
        ):
            await entity.async_turn_on()
        assert entity.is_on is False
        later.assert_not_called()
        entity.async_write_ha_state.assert_not_called()

    async def test_realtime_report_supersedes_optimistic_state(self) -> None:
        """An authoritative report releases the hold from a successful write."""
        entity = self._entity()
        cancel = Mock()
        with patch(
            "custom_components.eufy_sdk.entity.async_call_later", return_value=cancel
        ):
            await entity.async_turn_on()
        data = {
            "event": "propertyChanged",
            "deviceSn": "camera",
            "property": "light",
            "value": False,
        }
        apply_property_changed_event(self.coordinator, data)
        entity._handle_property_changed(SimpleNamespace(data=data))
        assert entity.is_on is False
        cancel.assert_called_once()
