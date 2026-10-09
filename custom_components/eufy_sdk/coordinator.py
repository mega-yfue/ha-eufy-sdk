"""DataUpdateCoordinator for eufy_sdk — owns the bridge connection + the device list."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    EufySdkApiClientAuthenticationError,
    EufySdkApiClientCommunicationError,
    EufySdkApiClientError,
)

if TYPE_CHECKING:
    from .data import EufySdkConfigEntry


class EufySdkDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Keep the bridge connected and expose the device list as `{sn: device}`."""

    config_entry: EufySdkConfigEntry

    # Anker Solix devices (separate account/backend), kept apart from the eufy `data`
    # so the eufy platforms never iterate them: `{sn: {productCode, name, category,
    # capabilities, values, ...}}`. Empty unless the bridge has SOLIX_* configured;
    # reassigned per-update, so the class-level {} is only an initial fallback. Live
    # values arrive via `solixReading` events.
    solix_devices: ClassVar[dict[str, dict]] = {}

    # One failed poll marks every entity `unavailable` until the NEXT poll: up to a
    # full `update_interval` of blackout for a single transient hiccup (a wedged WS,
    # a slow `list_devices`, a bridge still booting). After any failure, schedule one
    # fast retry so entities recover in under a minute without raising the
    # steady-state poll rate.
    _FAST_RETRY_S = 45
    _fast_retry_cancel: Any = None

    @callback
    def _schedule_fast_retry(self) -> None:
        """Arm one retry `_FAST_RETRY_S` from now, unless one is already pending."""
        if self._fast_retry_cancel is not None:
            return
        self._fast_retry_cancel = async_call_later(
            self.hass, self._FAST_RETRY_S, self._fast_retry_fire
        )

    @callback
    def _fast_retry_fire(self, _now: Any) -> None:
        """Run the pending fast retry."""
        self._fast_retry_cancel = None
        self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _clear_fast_retry(self) -> None:
        """Cancel a pending fast retry (a poll succeeded, or the entry is unloading)."""
        if self._fast_retry_cancel is not None:
            self._fast_retry_cancel()
            self._fast_retry_cancel = None

    async def async_shutdown(self) -> None:
        """Cancel a pending fast retry along with the coordinator."""
        self._clear_fast_retry()
        await super().async_shutdown()

    async def _async_update_data(self) -> dict[str, dict]:
        """Ensure the connection is up, confirm we're authed, and return the devices."""
        client = self.config_entry.runtime_data.client
        try:
            if not client.connected:
                await client.connect()
            auth = await client.auth_status()
            state = auth.get("state")
            if state in ("require_2fa", "require_captcha"):
                # Genuinely needs the user — start the reauth flow.
                msg = f"bridge needs re-authentication (state: {state})"
                raise ConfigEntryAuthFailed(msg)
            if state != "ok":
                # Transient: the bridge is still booting/logging in ("pending" after a
                # restart). Retry fast (below) instead of waiting a full interval or
                # freezing the entry in reauth.
                self._schedule_fast_retry()
                msg = f"bridge not ready yet (state: {state})"
                raise UpdateFailed(msg)
            devices = await client.list_devices()
        except EufySdkApiClientAuthenticationError as err:
            raise ConfigEntryAuthFailed(err) from err
        except EufySdkApiClientError as err:
            self._schedule_fast_retry()
            # A wedged WS won't clear `connected` on its own; drop it so the fast retry
            # reconnects fresh rather than timing out on the same dead socket. An error
            # the bridge *answered* with came over a healthy socket: dropping that one
            # would only cost a reconnect and a gap in the push stream.
            if isinstance(err, EufySdkApiClientCommunicationError):
                await client.reset_connection()
            raise UpdateFailed(err) from err
        # Solix is optional + independent: a hiccup must not fail the eufy update.
        try:
            solix = await client.list_solix_devices()
            self.solix_devices = {d["sn"]: d for d in solix if d.get("sn")}
        except EufySdkApiClientError:
            self.solix_devices = getattr(self, "solix_devices", {})
        self._clear_fast_retry()
        return {d["sn"]: d for d in devices if d.get("sn")}
