# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er

from custom_components.eufy_sdk import vacuum_display
from custom_components.eufy_sdk.entity import EufySdkPropertyEntity
from custom_components.eufy_sdk.select import EufySdkSelect
from custom_components.eufy_sdk.sensor import EufySdkPropertySensor

VACUUM = "T2351P0000000001"
CAMERA = "T8113P0000000001"


def _coordinator() -> Mock:
    coordinator = Mock()
    coordinator.data = {
        VACUUM: {"capabilities": ["vacuum_clean", "suction"], "state": {}},
        CAMERA: {"capabilities": ["camera"], "state": {}},
    }
    return coordinator


def _sensor(sn: str, name: str, **spec: object) -> EufySdkPropertyEntity:
    return EufySdkPropertySensor(
        _coordinator(), sn, {"name": name, "type": "number", **spec}
    )


class VacuumDisplayTests(unittest.TestCase):
    def test_a_primary_reading_sits_in_the_main_area(self):
        entity = _sensor(VACUUM, "battery", writable=False)
        self.assertIsNone(entity.entity_category)
        self.assertTrue(entity.entity_registry_enabled_default)

    def test_a_kept_reading_goes_under_diagnostics(self):
        entity = _sensor(VACUUM, "sideBrushHours", writable=False)
        self.assertEqual(entity.entity_category, EntityCategory.DIAGNOSTIC)
        self.assertTrue(entity.entity_registry_enabled_default)

    def test_an_unlisted_reading_starts_disabled(self):
        for name in ("voicePack", "mapLayers", "sceneCount", "wifiSignal"):
            with self.subTest(name=name):
                entity = _sensor(VACUUM, name, writable=False)
                self.assertFalse(entity.entity_registry_enabled_default)
                self.assertEqual(entity.entity_category, EntityCategory.DIAGNOSTIC)

    def test_a_writable_primary_control_leaves_configuration(self):
        spec = {
            "name": "suction",
            "type": "number",
            "kind": "enum",
            "writable": True,
            "enumValues": {"0": "Quiet", "1": "Standard"},
        }
        entity = EufySdkSelect(_coordinator(), VACUUM, spec)
        self.assertIsNone(entity.entity_category)

    def test_a_writable_unlisted_control_is_untouched(self):
        spec = {"name": "boostIq", "type": "bool", "writable": True}
        entity = EufySdkPropertyEntity(_coordinator(), VACUUM, spec)
        self.assertTrue(entity.entity_registry_enabled_default)
        self.assertIsNone(entity.entity_category)

    def test_both_clean_lines_share_one_label(self):
        for pair in (
            ("activity", "workStatus"),
            ("suction", "cleaningStrength"),
            ("mopLevel", "mopWater"),
        ):
            with self.subTest(pair=pair):
                a, b = (_sensor(VACUUM, name).name for name in pair)
                self.assertEqual(a, b)

    def test_a_non_vacuum_device_is_unaffected(self):
        entity = _sensor(CAMERA, "wifiSignal", writable=False)
        self.assertTrue(entity.entity_registry_enabled_default)
        self.assertEqual(entity.entity_category, EntityCategory.DIAGNOSTIC)
        self.assertEqual(entity.name, "Wifi Signal")


class DisableHiddenEntitiesTests(unittest.TestCase):
    def _run(
        self,
        minor_version: int,
        properties: dict,
        disabled_by: er.RegistryEntryDisabler | None = None,
    ) -> tuple[Mock, Mock]:
        entries = [
            SimpleNamespace(
                entity_id=f"sensor.{name}",
                unique_id=f"{sn}_{name}",
                disabled_by=disabled_by,
            )
            for sn, name in (
                (VACUUM, "voicePack"),
                (VACUUM, "battery"),
                (CAMERA, "wifiSignal"),
            )
        ]
        registry = Mock()
        hass = Mock()
        entry = SimpleNamespace(entry_id="e", minor_version=minor_version)
        with (
            patch.object(er, "async_get", return_value=registry),
            patch.object(er, "async_entries_for_config_entry", return_value=entries),
        ):
            vacuum_display.disable_hidden_entities(
                hass, entry, _coordinator().data, properties
            )
        return registry, hass

    PROPS = {  # noqa: RUF012
        VACUUM: [
            {"name": "voicePack", "type": "number", "writable": False},
            {"name": "battery", "type": "number", "writable": False},
        ],
        CAMERA: [{"name": "wifiSignal", "type": "number", "writable": False}],
    }

    def test_disables_only_hidden_vacuum_entities_then_bumps_the_entry(self):
        registry, hass = self._run(1, self.PROPS)
        registry.async_update_entity.assert_called_once_with(
            "sensor.voicePack", disabled_by=er.RegistryEntryDisabler.INTEGRATION
        )
        hass.config_entries.async_update_entry.assert_called_once()

    def test_runs_once(self):
        registry, hass = self._run(2, self.PROPS)
        registry.async_update_entity.assert_not_called()
        hass.config_entries.async_update_entry.assert_not_called()

    def test_leaves_an_entity_the_user_disabled_alone(self):
        registry, _ = self._run(1, self.PROPS, er.RegistryEntryDisabler.USER)
        registry.async_update_entity.assert_not_called()

    def test_retries_when_a_vacuum_has_no_properties_yet(self):
        _, hass = self._run(1, {CAMERA: self.PROPS[CAMERA]})
        hass.config_entries.async_update_entry.assert_not_called()


if __name__ == "__main__":
    unittest.main()
