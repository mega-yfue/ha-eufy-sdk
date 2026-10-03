"""Regression tests for bitfield switch writes."""

# ruff: noqa: INP001, SLF001

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from custom_components.eufy_sdk.switch import EufyBitmaskSwitch


class TestEufyBitmaskSwitch(IsolatedAsyncioTestCase):
    """Verify that per-bit writes preserve device-specific mask bits."""

    async def test_write_preserves_device_specific_bits(self) -> None:
        """Change only the requested bit, never inject the fallback base."""
        entity = EufyBitmaskSwitch.__new__(EufyBitmaskSwitch)
        entity._bit = 0x4
        entity._base = 0x30000
        entity.write = AsyncMock()

        entity._mask = lambda: 0x8007
        await entity.async_turn_off()
        entity.write.assert_awaited_once_with(0x8003)

        entity.write.reset_mock()
        entity._mask = lambda: 0x8003
        await entity.async_turn_on()
        entity.write.assert_awaited_once_with(0x8007)
