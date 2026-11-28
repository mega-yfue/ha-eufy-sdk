# ruff: noqa: ANN201, D100, D102, INP001, PT009, PT027, SLF001

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.eufy_sdk import button, select
from custom_components.eufy_sdk.button import EufySdkTossTreatButton
from custom_components.eufy_sdk.select import EufySdkTossDistanceSelect
from custom_components.eufy_sdk.sensor import EufySdkPropertySensor

DOG_CAMERA = "T7200P0000000001"
OTHER_DOG_CAMERA = "T7203P0000000001"
CAMERA = "T8161P0000000001"


def _coordinator() -> Mock:
    coord = Mock()
    coord.data = {
        # A dog camera the bridge says can toss.
        DOG_CAMERA: {
            "name": "Dog cam",
            "capabilities": ["camera", "treat"],
            "canToss": True,
        },
        # A sibling model: `treat` for its last-toss reading, but no toss.
        OTHER_DOG_CAMERA: {
            "name": "Other dog cam",
            "capabilities": ["camera", "treat"],
            "canToss": False,
        },
        CAMERA: {"name": "Garden", "capabilities": ["camera", "battery"]},
    }
    coord.solix_devices = {}
    runtime = coord.config_entry.runtime_data
    runtime.client.action = AsyncMock(return_value=None)
    runtime.toss_distance = {}
    runtime.properties = {}
    return coord


def _entry(coord: Mock) -> Mock:
    entry = Mock()
    entry.runtime_data = coord.config_entry.runtime_data
    entry.runtime_data.coordinator = coord
    return entry


def _select(coord: Mock) -> EufySdkTossDistanceSelect:
    entity = EufySdkTossDistanceSelect(coord, DOG_CAMERA)
    entity.hass = Mock()
    entity.async_get_last_state = AsyncMock(return_value=None)
    entity.async_write_ha_state = Mock()
    return entity


async def _add(entity: EufySdkTossDistanceSelect, last_state: str | None) -> None:
    last = None if last_state is None else Mock(state=last_state)
    entity.async_get_last_state = AsyncMock(return_value=last)
    with patch.object(CoordinatorEntity, "async_added_to_hass", AsyncMock()):
        await entity.async_added_to_hass()


class TossTreatButtonTest(unittest.IsolatedAsyncioTestCase):
    """One press is one toss request, at the distance the select holds."""

    async def test_only_a_device_the_bridge_says_can_toss_gets_the_button(self):
        coord = _coordinator()
        added: list = []
        await button.async_setup_entry(Mock(), _entry(coord), added.extend)

        tosses = [e for e in added if isinstance(e, EufySdkTossTreatButton)]
        self.assertEqual([e._sn for e in tosses], [DOG_CAMERA])
        self.assertEqual(tosses[0].unique_id, f"{DOG_CAMERA}_toss_treat")
        self.assertEqual(tosses[0].translation_key, "toss_treat")

    async def test_press_sends_one_toss_at_the_selected_distance(self):
        coord = _coordinator()
        coord.config_entry.runtime_data.toss_distance[DOG_CAMERA] = "far"

        await EufySdkTossTreatButton(coord, DOG_CAMERA).async_press()

        client = coord.config_entry.runtime_data.client
        client.action.assert_awaited_once_with(DOG_CAMERA, "toss", "far")

    async def test_press_before_any_choice_tosses_at_middle(self):
        coord = _coordinator()

        await EufySdkTossTreatButton(coord, DOG_CAMERA).async_press()

        client = coord.config_entry.runtime_data.client
        client.action.assert_awaited_once_with(DOG_CAMERA, "toss", "middle")

    async def test_a_failed_press_raises_and_is_not_sent_again(self):
        coord = _coordinator()
        client = coord.config_entry.runtime_data.client
        client.action = AsyncMock(side_effect=TimeoutError("device.action: timed out"))

        with self.assertRaises(TimeoutError):
            await EufySdkTossTreatButton(coord, DOG_CAMERA).async_press()

        # A repeat is a second treat: the failure reaches the user, nothing resends.
        self.assertEqual(client.action.await_count, 1)

    async def test_each_press_reads_the_distance_afresh(self):
        coord = _coordinator()
        runtime = coord.config_entry.runtime_data
        toss = EufySdkTossTreatButton(coord, DOG_CAMERA)

        runtime.toss_distance[DOG_CAMERA] = "near"
        await toss.async_press()
        runtime.toss_distance[DOG_CAMERA] = "far"
        await toss.async_press()

        sent = [call.args for call in runtime.client.action.await_args_list]
        self.assertEqual(
            sent, [(DOG_CAMERA, "toss", "near"), (DOG_CAMERA, "toss", "far")]
        )


class TossDistanceSelectTest(unittest.IsolatedAsyncioTestCase):
    """The distance is a local choice: it is kept, restored and never sent by itself."""

    async def test_only_a_device_the_bridge_says_can_toss_gets_the_select(self):
        coord = _coordinator()
        added: list = []
        await select.async_setup_entry(Mock(), _entry(coord), added.extend)

        distances = [e for e in added if isinstance(e, EufySdkTossDistanceSelect)]
        self.assertEqual([e._sn for e in distances], [DOG_CAMERA])
        self.assertEqual(distances[0].unique_id, f"{DOG_CAMERA}_toss_distance")
        self.assertEqual(distances[0].translation_key, "toss_distance")

    async def test_offers_the_three_distances_and_starts_at_middle(self):
        coord = _coordinator()
        entity = _select(coord)
        await _add(entity, None)

        self.assertEqual(entity.options, ["near", "middle", "far"])
        self.assertEqual(entity.current_option, "middle")
        runtime = coord.config_entry.runtime_data
        self.assertEqual(runtime.toss_distance, {DOG_CAMERA: "middle"})

    async def test_restores_the_last_choice_after_a_restart(self):
        coord = _coordinator()
        entity = _select(coord)
        await _add(entity, "far")

        self.assertEqual(entity.current_option, "far")
        runtime = coord.config_entry.runtime_data
        self.assertEqual(runtime.toss_distance, {DOG_CAMERA: "far"})

    async def test_a_stored_state_that_is_not_a_distance_falls_back_to_middle(self):
        for stored in ("unavailable", "unknown", "medium", ""):
            with self.subTest(stored=stored):
                coord = _coordinator()
                entity = _select(coord)
                await _add(entity, stored)
                self.assertEqual(entity.current_option, "middle")

    async def test_choosing_a_distance_tosses_nothing(self):
        coord = _coordinator()
        entity = _select(coord)
        await _add(entity, None)

        await entity.async_select_option("near")

        runtime = coord.config_entry.runtime_data
        self.assertEqual(entity.current_option, "near")
        self.assertEqual(runtime.toss_distance, {DOG_CAMERA: "near"})
        entity.async_write_ha_state.assert_called_once_with()
        runtime.client.action.assert_not_awaited()

    async def test_an_unknown_distance_is_refused_and_the_choice_kept(self):
        coord = _coordinator()
        entity = _select(coord)
        await _add(entity, "far")

        with self.assertRaises(HomeAssistantError):
            await entity.async_select_option("medium")

        runtime = coord.config_entry.runtime_data
        self.assertEqual(entity.current_option, "far")
        self.assertEqual(runtime.toss_distance, {DOG_CAMERA: "far"})

    async def test_the_button_tosses_at_the_distance_the_select_holds(self):
        coord = _coordinator()
        entity = _select(coord)
        await _add(entity, None)
        await entity.async_select_option("far")

        await EufySdkTossTreatButton(coord, DOG_CAMERA).async_press()

        client = coord.config_entry.runtime_data.client
        client.action.assert_awaited_once_with(DOG_CAMERA, "toss", "far")


LAST_TOSS = {
    "name": "lastTossAt",
    "type": "number",
    "kind": "timestamp",
    "writable": False,
}


def _last_toss(value: object) -> EufySdkPropertySensor:
    coord = Mock()
    state = {} if value is None else {"lastTossAt": value}
    coord.data = {DOG_CAMERA: {"capabilities": ["camera", "treat"], "state": state}}
    return EufySdkPropertySensor(coord, DOG_CAMERA, LAST_TOSS)


class TimestampSensorTest(unittest.TestCase):
    """A `timestamp` property reads as an instant, not as a ten-digit number."""

    def test_unix_seconds_become_a_timestamp_sensor_value(self):
        sensor = _last_toss(1_795_723_547)
        self.assertEqual(sensor.device_class, SensorDeviceClass.TIMESTAMP)
        self.assertEqual(
            sensor.native_value, datetime.fromtimestamp(1_795_723_547, tz=UTC)
        )

    def test_seconds_sent_as_text_read_the_same(self):
        self.assertEqual(
            _last_toss("1795723547").native_value,
            datetime.fromtimestamp(1_795_723_547, tz=UTC),
        )

    def test_nothing_reported_yet_is_unknown(self):
        for value in (None, 0, "0", "", "never", True, [1_795_723_547]):
            with self.subTest(value=value):
                self.assertIsNone(_last_toss(value).native_value)

    def test_a_value_that_is_no_real_time_is_unknown(self):
        for value in (10**30, 1e20, "1e400", float("inf"), float("nan")):
            with self.subTest(value=value):
                self.assertIsNone(_last_toss(value).native_value)

    def test_other_number_properties_keep_their_plain_value(self):
        coord = Mock()
        coord.data = {DOG_CAMERA: {"capabilities": [], "state": {"wifiRssi": -51}}}
        spec = {"name": "wifiRssi", "type": "number", "kind": "dbm", "writable": False}
        sensor = EufySdkPropertySensor(coord, DOG_CAMERA, spec)
        self.assertEqual(sensor.native_value, -51)
        self.assertEqual(sensor.device_class, SensorDeviceClass.SIGNAL_STRENGTH)


if __name__ == "__main__":
    unittest.main()
