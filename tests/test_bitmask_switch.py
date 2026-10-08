"""Regression tests for bitfield switch writes."""

# ruff: noqa: INP001, S101, SLF001

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from custom_components.eufy_sdk.switch import EufyBitmaskSwitch

FALLBACK_BASE = 0x30000


class TestEufyBitmaskSwitch(IsolatedAsyncioTestCase):
    """Verify that bitfield writes preserve the device's actual mask."""

    def _entity(
        self,
        value: float | str | None,
    ) -> EufyBitmaskSwitch:
        entity = EufyBitmaskSwitch.__new__(EufyBitmaskSwitch)
        entity._sn = "camera"
        entity._prop = "aiDetectType"
        entity._bit = 0x4
        entity._base = FALLBACK_BASE
        entity._assumed_value = None
        entity.coordinator = Mock(
            data={
                "camera": {
                    "state": {
                        "aiDetectType": value,
                    }
                }
            }
        )
        entity.write = AsyncMock()
        return entity

    def test_mask_uses_reported_numeric_state(self) -> None:
        """Use zero, high bits and numeric coercion without injecting the fallback."""
        for reported, expected in (
            (0, 0),
            (0x8007, 0x8007),
            (float(0x8007), 0x8007),
        ):
            with self.subTest(reported=reported):
                assert self._entity(reported)._mask() == expected

    def test_mask_falls_back_for_non_numeric_state(self) -> None:
        """Absent and non-numeric state use the configured fallback base."""
        for reported in (None, "32775"):
            with self.subTest(reported=reported):
                assert self._entity(reported)._mask() == FALLBACK_BASE

    async def test_write_preserves_device_specific_bits(self) -> None:
        """Change only the requested bit using real coordinator state."""
        entity = self._entity(0x8007)

        await entity.async_turn_off()
        entity.write.assert_awaited_once_with(0x8003)

        entity.write.reset_mock()
        entity.coordinator.data["camera"]["state"]["aiDetectType"] = 0x8003

        await entity.async_turn_on()
        entity.write.assert_awaited_once_with(0x8007)

    async def test_zero_mask_does_not_inject_fallback_base(self) -> None:
        """A reported zero is authoritative rather than a missing value."""
        entity = self._entity(0)

        await entity.async_turn_on()

        entity.write.assert_awaited_once_with(0x4)
