# ruff: noqa: ANN201, D100, D102, INP001, PT009, SLF001

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from custom_components.eufy_sdk.binary_sensor import EufySolixConnectivitySensor
from custom_components.eufy_sdk.const import DOMAIN, EVENT_TYPE, SOLIX_READING_EVENT
from custom_components.eufy_sdk.number import (
    SOC_CHARGE,
    SOC_DISCHARGE,
    EufySolixSocLimitNumber,
)
from custom_components.eufy_sdk.select import EufySolixScreenOffSelect
from custom_components.eufy_sdk.sensor import (
    SOLIX_BATTERY_METRICS,
    EufySolixBatteryStatusSensor,
    EufySolixChannelSensor,
    EufySolixModeSensor,
    EufySolixSensor,
    EufySolixTimeToEmptySensor,
    EufySolixTimeToFullSensor,
)
from custom_components.eufy_sdk.switch import EufySolixLightSwitch

SN = "AE103P0000000001"
OTHER_SN = "AE103P0000000002"


def coordinator(values: dict | None = None) -> Mock:
    """Build a coordinator stub carrying one Solix device with these values."""
    coord = Mock()
    coord.solix_devices = {
        SN: {
            "name": "Solarbank",
            "productCode": "AE103",
            "firmware": "1.0",
            "values": dict(values or {}),
        }
    }
    coord.config_entry.options = {}
    coord.config_entry.runtime_data.client = Mock()
    return coord


def reading(values: dict, *, sn: str = SN, event: str = SOLIX_READING_EVENT):
    """Build a bus event as the integration re-fires a bridge `solixReading`."""
    return SimpleNamespace(
        event_type=EVENT_TYPE, data={"event": event, "deviceSn": sn, "values": values}
    )


def quiet[T](entity: T) -> T:
    """Detach state writes from a (non-existent) hass."""
    entity.async_write_ha_state = Mock()
    return entity


class SolixIdentityTests(unittest.TestCase):
    """Each Solix entity keeps its unique_id and joins the one `solix:<sn>` device."""

    def test_unique_ids_are_unchanged(self):
        c = coordinator()
        metric = next(iter(SOLIX_BATTERY_METRICS))
        expected = {
            EufySolixSensor(c, SN, metric, SOLIX_BATTERY_METRICS[metric]): metric,
            EufySolixChannelSensor(c, SN, "channel_a8"): "channel_a8",
            EufySolixBatteryStatusSensor(c, SN): "battery_status",
            EufySolixModeSensor(c, SN): "mode",
            EufySolixTimeToFullSensor(c, SN): "time_to_full",
            EufySolixTimeToEmptySensor(c, SN): "time_to_empty",
            EufySolixLightSwitch(c, SN): "ambient_light",
            EufySolixScreenOffSelect(c, SN): "screen_off_time",
            EufySolixSocLimitNumber(c, SN, SOC_DISCHARGE): "discharge_limit",
            EufySolixSocLimitNumber(c, SN, SOC_CHARGE): "charge_limit",
            EufySolixConnectivitySensor(c, SN): "connectivity",
        }
        for entity, key in expected.items():
            with self.subTest(entity=type(entity).__name__):
                self.assertEqual(entity.unique_id, f"solix_{SN}_{key}")
                self.assertEqual(
                    entity.device_info["identifiers"], {(DOMAIN, f"solix:{SN}")}
                )
                self.assertEqual(entity.device_info["manufacturer"], "Anker Solix")


class SolixEntityBehaviourTests(unittest.TestCase):
    """The shared telemetry plumbing, exercised through a plain metric sensor."""

    def setUp(self):
        self.coord = coordinator({"batterySoc": 40})
        meta = SOLIX_BATTERY_METRICS.get("batterySoc", {"name": "Battery"})
        self.sensor = quiet(EufySolixSensor(self.coord, SN, "batterySoc", meta))

    def test_seeds_from_the_snapshot(self):
        self.assertEqual(self.sensor.native_value, 40)

    def test_adopts_a_reading_for_this_device(self):
        self.sensor._handle_solix_event(reading({"batterySoc": 55}))
        self.assertEqual(self.sensor.native_value, 55)
        self.sensor.async_write_ha_state.assert_called_once()

    def test_ignores_other_devices_and_other_events(self):
        self.sensor._handle_solix_event(reading({"batterySoc": 55}, sn=OTHER_SN))
        self.sensor._handle_solix_event(reading({"batterySoc": 55}, event="other"))
        self.assertEqual(self.sensor.native_value, 40)
        self.sensor.async_write_ha_state.assert_not_called()

    def test_an_unchanged_value_does_not_rewrite_state(self):
        self.sensor._handle_solix_event(reading({"batterySoc": 40}))
        self.sensor.async_write_ha_state.assert_not_called()

    def test_snapshot_none_never_clears_a_known_value(self):
        self.coord.solix_devices[SN]["values"] = {"batterySoc": None}
        self.sensor._handle_solix_snapshot()
        self.assertEqual(self.sensor.native_value, 40)
        self.coord.solix_devices[SN]["values"] = {"batterySoc": 61}
        self.sensor._handle_solix_snapshot()
        self.assertEqual(self.sensor.native_value, 61)

    def test_available_only_while_the_bridge_lists_the_device(self):
        self.assertTrue(self.sensor.available)
        self.coord.solix_devices = {}
        self.assertFalse(self.sensor.available)


class SolixEntitySpecificsTests(unittest.IsolatedAsyncioTestCase):
    """Per-entity rules that sit on top of the shared plumbing."""

    async def test_light_switch_is_optimistic_then_follows_telemetry(self):
        c = coordinator({"ambientLightOn": 0})
        c.config_entry.runtime_data.client.set_solix_light = AsyncMock()
        switch = quiet(EufySolixLightSwitch(c, SN))
        self.assertFalse(switch.is_on)
        await switch.async_turn_on()
        c.config_entry.runtime_data.client.set_solix_light.assert_awaited_once_with(
            SN, on=True
        )
        self.assertTrue(switch.is_on)
        switch._handle_solix_event(reading({"ambientLightOn": 0}))
        self.assertFalse(switch.is_on)

    def test_select_keeps_its_label_on_an_unknown_index(self):
        select = quiet(EufySolixScreenOffSelect(coordinator(), SN))
        select._handle_solix_event(reading({"displayTimeoutIndex": 3}))
        self.assertEqual(select.current_option, "30s")
        select._handle_solix_event(reading({"displayTimeoutIndex": 99}))
        self.assertEqual(select.current_option, "30s")

    async def test_soc_limit_reads_telemetry_http_and_writes(self):
        c = coordinator({"dischargeLimit": 10})
        client = c.config_entry.runtime_data.client
        client.get_solix_soc_params = AsyncMock(
            return_value={"dischargeLowerLimit": 15}
        )
        client.set_solix_soc_limits = AsyncMock()
        number = quiet(EufySolixSocLimitNumber(c, SN, SOC_DISCHARGE))
        self.assertEqual(number.native_value, 10.0)
        number._handle_solix_event(reading({"dischargeLimit": "junk"}))
        self.assertEqual(number.native_value, 10.0)
        await number._fetch_limit()
        self.assertEqual(number.native_value, 15.0)
        await number.async_set_native_value(5)
        client.set_solix_soc_limits.assert_awaited_once_with(SN, discharge=5)
        self.assertEqual(number.native_value, 5.0)

    def test_countdowns_follow_the_watched_inputs(self):
        c = coordinator({"batterySoc": 50})
        full = quiet(EufySolixTimeToFullSensor(c, SN))
        empty = quiet(EufySolixTimeToEmptySensor(c, SN))
        self.assertIsNone(full.native_value)
        full._handle_solix_event(reading({"chargePower": 500}))
        self.assertEqual(full.native_value, "5:00:00")
        empty._handle_solix_event(reading({"batteryPower": -250}))
        self.assertEqual(empty.native_value, "10:00:00")

    def test_battery_status_and_mode(self):
        c = coordinator({"batteryPower": -300, "mode": 99})
        status = quiet(EufySolixBatteryStatusSensor(c, SN))
        mode = quiet(EufySolixModeSensor(c, SN))
        self.assertEqual(status.native_value, "Discharging")
        self.assertEqual(mode.native_value, "Mode 99")
        mode._handle_solix_event(reading({"mode": 2}))
        self.assertEqual(mode.native_value, "Self-Consumption")
