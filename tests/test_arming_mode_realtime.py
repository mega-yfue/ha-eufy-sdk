# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, S101

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock

_MODULE_PATH = Path(__file__).parents[1] / "custom_components/eufy_sdk/arming_sync.py"
_SPEC = importlib.util.spec_from_file_location("arming_sync", _MODULE_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)


class ArmingModeRealtimeTests(unittest.TestCase):
    def test_valued_event_updates_matching_device(self):
        coordinator = Mock(
            data={
                "homebase": {"state": {"armingMode": 1}},
                "camera": {"state": {"motion": False}},
            }
        )

        changed = _MODULE.apply_arming_mode_event(
            coordinator,
            {"event": "armingModeChanged", "deviceSn": "homebase", "mode": 4},
        )

        self.assertTrue(changed)
        self.assertEqual(coordinator.data["homebase"]["state"]["armingMode"], 4)
        coordinator.async_update_listeners.assert_called_once_with()
        self.assertEqual(coordinator.data["camera"], {"state": {"motion": False}})

    def test_valued_event_for_unknown_device_is_ignored(self):
        coordinator = Mock(data={"homebase": {"state": {"armingMode": 1}}})

        changed = _MODULE.apply_arming_mode_event(
            coordinator,
            {"event": "armingModeChanged", "deviceSn": "missing", "mode": 4},
        )

        self.assertFalse(changed)
        self.assertEqual(coordinator.data["homebase"]["state"]["armingMode"], 1)
        coordinator.async_update_listeners.assert_not_called()


if __name__ == "__main__":
    unittest.main()
