# ruff: noqa: ANN201, D100, D102, INP001, PT009, SLF001

import asyncio
import unittest
from unittest.mock import Mock

from custom_components.eufy_sdk.binary_sensor import (
    EufyOnlineBinarySensor,
    EufyStreamingBinarySensor,
    async_setup_entry,
)
from custom_components.eufy_sdk.entity import device_offline

SN = "T8160P0000000001"
INDOOR_SN = "T8400P0000000001"


def coordinator(*, device_status: object = True, with_status: bool = True) -> Mock:
    """Build a coordinator stub with one battery camera and one indoor camera."""
    state = {"battery": 77}
    if with_status:
        state["deviceStatus"] = device_status
    coord = Mock()
    coord.last_update_success = True
    coord.solix_devices = {}
    coord.data = {
        SN: {
            "sn": SN,
            "name": "Garden",
            "model": "T8160",
            "codec": "camera",
            "capabilities": ["camera", "battery"],
            "stream": f"/stream/{SN}",
            "state": state,
        },
        # Indoor cameras don't report deviceStatus at all.
        INDOOR_SN: {
            "sn": INDOOR_SN,
            "name": "Hall",
            "model": "T8400",
            "codec": "camera",
            "capabilities": ["camera"],
            "stream": f"/stream/{INDOOR_SN}",
            "state": {"statusLed": True},
        },
    }
    return coord


class DeviceOfflineTests(unittest.TestCase):
    """Only an explicit `deviceStatus: False` counts as offline."""

    def test_false_is_offline(self):
        self.assertTrue(device_offline({"state": {"deviceStatus": False}}))

    def test_true_missing_or_none_is_not_offline(self):
        self.assertFalse(device_offline({"state": {"deviceStatus": True}}))
        self.assertFalse(device_offline({"state": {"deviceStatus": None}}))
        self.assertFalse(device_offline({"state": {}}))
        self.assertFalse(device_offline({}))
        self.assertFalse(device_offline(None))


class AvailabilityTests(unittest.TestCase):
    """A device's entities go unavailable while the cloud reports it offline."""

    def test_device_entity_follows_device_status(self):
        online = EufyStreamingBinarySensor(coordinator(device_status=True), SN)
        offline = EufyStreamingBinarySensor(coordinator(device_status=False), SN)
        self.assertTrue(online.available)
        self.assertFalse(offline.available)

    def test_device_without_status_stays_available(self):
        c = coordinator(with_status=False)
        self.assertTrue(EufyStreamingBinarySensor(c, SN).available)
        self.assertTrue(EufyStreamingBinarySensor(c, INDOOR_SN).available)

    def test_bridge_down_still_wins(self):
        c = coordinator(device_status=True)
        c.last_update_success = False
        self.assertFalse(EufyStreamingBinarySensor(c, SN).available)


class OnlineSensorTests(unittest.TestCase):
    """The Online sensor shows the state and stays available while offline."""

    def test_reports_status_and_stays_available(self):
        on = EufyOnlineBinarySensor(coordinator(device_status=True), SN)
        off = EufyOnlineBinarySensor(coordinator(device_status=False), SN)
        self.assertTrue(on.is_on)
        self.assertFalse(off.is_on)
        self.assertTrue(off.available)

    def test_unknown_until_reported(self):
        sensor = EufyOnlineBinarySensor(coordinator(device_status=None), SN)
        self.assertIsNone(sensor.is_on)

    def test_unique_id(self):
        self.assertEqual(
            EufyOnlineBinarySensor(coordinator(), SN).unique_id, f"{SN}_online"
        )

    def test_created_only_for_devices_reporting_status(self):
        entry = Mock()
        entry.runtime_data.coordinator = coordinator(device_status=True)
        entry.runtime_data.properties = {}
        added: list = []
        asyncio.run(async_setup_entry(Mock(), entry, added.extend))
        online = [e for e in added if isinstance(e, EufyOnlineBinarySensor)]
        self.assertEqual([e._sn for e in online], [SN])


if __name__ == "__main__":
    unittest.main()
