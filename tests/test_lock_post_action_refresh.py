"""The delayed post-action reconcile must run on the event loop."""

# ruff: noqa: INP001, S101, SLF001

from __future__ import annotations

from unittest import TestCase
from unittest.mock import Mock

from homeassistant.core import is_callback

from custom_components.eufy_sdk.lock import EufySdkLock

LOCK = "T8530P0000000001"


class TestLockPostActionRefresh(TestCase):
    """Guard the scheduling contract of the delayed reconcile."""

    def setUp(self) -> None:
        """Bind a lock entity with an initially unknown snapshot."""
        self.coordinator = Mock(data={LOCK: {"state": {}}})
        self.entity = EufySdkLock(self.coordinator, LOCK)

    def test_delayed_refresh_runs_on_the_event_loop(self) -> None:
        """async_call_later must not push the reconcile into the executor."""
        assert is_callback(self.entity._post_action_refresh)
