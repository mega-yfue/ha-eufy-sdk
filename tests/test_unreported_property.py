# ruff: noqa: ANN201, D100, D101, D102, INP001, PT009

import unittest
from unittest.mock import Mock

from custom_components.eufy_sdk.entity import EufySdkPropertyEntity

CAMERA = "T8113P0000000001"


def _entity(spec: dict) -> EufySdkPropertyEntity:
    coordinator = Mock()
    coordinator.data = {CAMERA: {"capabilities": ["camera"], "state": {}}}
    return EufySdkPropertyEntity(coordinator, CAMERA, spec)


class UnreportedPropertyTests(unittest.TestCase):
    def test_a_read_only_property_never_reported_starts_disabled(self):
        # ha-eufy-sdk#64: such a property would show unknown for good.
        spec = {"name": "solarIntensity", "type": "number", "writable": False}
        self.assertFalse(
            _entity({**spec, "reported": False}).entity_registry_enabled_default
        )

    def test_a_reported_or_unflagged_read_only_property_stays_enabled(self):
        spec = {"name": "battery", "type": "number", "writable": False}
        self.assertTrue(
            _entity({**spec, "reported": True}).entity_registry_enabled_default
        )
        self.assertTrue(_entity(spec).entity_registry_enabled_default)  # older bridge

    def test_a_writable_property_stays_enabled_even_if_never_reported(self):
        spec = {"name": "recordingMode", "type": "enum", "writable": True}
        self.assertTrue(
            _entity({**spec, "reported": False}).entity_registry_enabled_default
        )


if __name__ == "__main__":
    unittest.main()
