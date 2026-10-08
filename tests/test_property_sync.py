# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, S101

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock

_MODULE_PATH = Path(__file__).parents[1] / "custom_components/eufy_sdk/property_sync.py"
_SPEC = importlib.util.spec_from_file_location("property_sync", _MODULE_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)


class PropertySyncTests(unittest.TestCase):
    def test_valued_property_change_updates_matching_device(self):
        coordinator = Mock(
            data={
                "camera": {
                    "state": {
                        "aiDetectType": 0x3000B,
                        "statusLed": True,
                    }
                }
            }
        )

        result = _MODULE.apply_property_changed_event(
            coordinator,
            {
                "event": "propertyChanged",
                "deviceSn": "camera",
                "property": "aiDetectType",
                "value": 0x3000F,
            },
        )

        self.assertEqual(result, "applied")
        self.assertEqual(
            coordinator.data["camera"]["state"]["aiDetectType"],
            0x3000F,
        )
        self.assertTrue(coordinator.data["camera"]["state"]["statusLed"])
        coordinator.async_update_listeners.assert_called_once_with()

    def test_valueless_property_change_requests_refresh(self):
        coordinator = Mock(data={"camera": {"state": {"aiDetectType": 0x3000B}}})

        result = _MODULE.apply_property_changed_event(
            coordinator,
            {
                "event": "propertyChanged",
                "deviceSn": "camera",
                "property": "aiDetectType",
            },
        )

        self.assertEqual(result, "refresh")
        self.assertEqual(
            coordinator.data["camera"]["state"]["aiDetectType"],
            0x3000B,
        )
        coordinator.async_update_listeners.assert_not_called()

    def test_unknown_device_is_ignored(self):
        coordinator = Mock(data={"camera": {"state": {}}})

        result = _MODULE.apply_property_changed_event(
            coordinator,
            {
                "event": "propertyChanged",
                "deviceSn": "missing",
                "property": "statusLed",
                "value": False,
            },
        )

        self.assertIsNone(result)
        coordinator.async_update_listeners.assert_not_called()


if __name__ == "__main__":
    unittest.main()
