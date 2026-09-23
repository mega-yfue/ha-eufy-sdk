# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009, S101

import importlib.util
import unittest
from pathlib import Path

_MODULE_PATH = Path(__file__).parents[1] / "custom_components/eufy_sdk/alarm_logic.py"
_SPEC = importlib.util.spec_from_file_location("alarm_logic", _MODULE_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)


class AlarmLogicTests(unittest.TestCase):
    def test_explicit_modes_map_to_alarm_states(self):
        self.assertEqual(
            [_MODULE.alarm_state_for_raw(raw) for raw in (0, 1, 3, 4, 5, 63)],
            [
                _MODULE.AlarmState.ARMED_AWAY,
                _MODULE.AlarmState.ARMED_HOME,
                _MODULE.AlarmState.ARMED_CUSTOM_BYPASS,
                _MODULE.AlarmState.ARMED_NIGHT,
                _MODULE.AlarmState.ARMED_VACATION,
                _MODULE.AlarmState.DISARMED,
            ],
        )

    def test_unmapped_modes_remain_unknown(self):
        for raw in (2, 6, 47, None, True, 3.9, "custom2"):
            self.assertIsNone(_MODULE.alarm_state_for_raw(raw))


if __name__ == "__main__":
    unittest.main()
