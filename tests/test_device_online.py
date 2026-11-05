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
    """With no liveness facts (older bridge) the legacy `deviceStatus` decides."""

    def test_false_is_offline(self):
        self.assertTrue(device_offline({"state": {"deviceStatus": False}}))

    def test_true_missing_or_none_is_not_offline(self):
        self.assertFalse(device_offline({"state": {"deviceStatus": True}}))
        self.assertFalse(device_offline({"state": {"deviceStatus": None}}))
        self.assertFalse(device_offline({"state": {}}))
        self.assertFalse(device_offline({}))
        self.assertFalse(device_offline(None))


class LivenessFactTests(unittest.TestCase):
    """When the bridge forwards the SDK liveness facts, they beat `deviceStatus`."""

    NOW = 1_700_000_000_000

    def test_explicit_availability_is_authoritative(self):
        # It overrides even a contradicting deviceStatus either way.
        self.assertTrue(
            device_offline(
                {"availability": "unavailable", "state": {"deviceStatus": True}},
                now_ms=self.NOW,
            )
        )
        self.assertFalse(
            device_offline(
                {"availability": "available", "state": {"deviceStatus": False}},
                now_ms=self.NOW,
            )
        )

    def test_an_active_stream_means_reachable(self):
        # #144: a standalone camera whose deviceStatus is false but is streaming now.
        rec = {
            "codec": "camera",
            "power": "battery",
            "streaming": True,
            "state": {"deviceStatus": False},
        }
        self.assertFalse(device_offline(rec, now_ms=self.NOW))

    def test_a_live_p2p_transport_means_reachable_despite_stale_last_seen(self):
        # #121: a standalone mains camera on a live session sends only keepalives, so
        # lastSeenMs is stale and deviceStatus is false, yet it is plainly reachable.
        rec = {
            "codec": "camera",
            "power": "wired",
            "p2pReachable": True,
            "lastSeenMs": self.NOW - 3 * 60 * 60 * 1000,
            "state": {"deviceStatus": False},
        }
        self.assertFalse(device_offline(rec, now_ms=self.NOW))

    def test_a_recently_seen_camera_is_not_offline_despite_devicestatus(self):
        # #144: param 1131 is false, but it reported a minute ago, so it is reachable.
        rec = {
            "codec": "camera",
            "power": "battery",
            "lastSeenMs": self.NOW - 60_000,
            "state": {"deviceStatus": False},
        }
        self.assertFalse(device_offline(rec, now_ms=self.NOW))

    def test_a_long_silent_battery_camera_is_offline(self):
        # #133: powered off — no report for well over the battery-camera window.
        rec = {
            "codec": "camera",
            "power": "battery",
            "lastSeenMs": self.NOW - 7 * 60 * 60 * 1000,
            "state": {"deviceStatus": True},
        }
        self.assertTrue(device_offline(rec, now_ms=self.NOW))

    def test_a_mains_camera_uses_the_short_window(self):
        rec = {
            "codec": "camera",
            "power": "wired",
            "lastSeenMs": self.NOW - 45 * 60 * 1000,
        }
        self.assertTrue(device_offline(rec, now_ms=self.NOW))
        rec_fresh = {
            "codec": "camera",
            "power": "wired",
            "lastSeenMs": self.NOW - 60_000,
        }
        self.assertFalse(device_offline(rec_fresh, now_ms=self.NOW))

    def test_a_sensor_is_never_offline_from_staleness(self):
        # Silent by design — days without a report is healthy.
        rec = {"codec": "sensor", "lastSeenMs": self.NOW - 3 * 24 * 60 * 60 * 1000}
        self.assertFalse(device_offline(rec, now_ms=self.NOW))


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
