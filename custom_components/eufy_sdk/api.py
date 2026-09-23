"""
WebSocket client for the ha-eufy-sdk bridge.

The bridge holds the eufy account (and drives 2FA/captcha); this client speaks its
JSON protocol over one WebSocket. Request/response is `{id, cmd}` -> `{id, ok}`;
unsolicited `{event}` messages go to `on_event`. See the bridge's `docs/ws-protocol.md`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from collections.abc import Callable


class EufySdkApiClientError(Exception):
    """A general bridge error."""


class EufySdkApiClientCommunicationError(EufySdkApiClientError):
    """The bridge could not be reached / spoke unexpectedly."""


class EufySdkApiClientAuthenticationError(EufySdkApiClientError):
    """The bridge is not authenticated (needs 2FA/captcha) or rejected a command."""


class EufySdkApiClient:
    """A connection to one ha-eufy-sdk bridge."""

    def __init__(
        self,
        host: str,
        port: int,
        session: aiohttp.ClientSession,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        on_reconnect: Callable[[], None] | None = None,
    ) -> None:
        """Store the bridge address; the connection is opened by `connect`."""
        # int() the port defensively: HA's NumberSelector yields a float, which
        # would make an invalid URL like ws://host:3012.0/ws.
        self._url = f"ws://{host}:{int(port)}/ws"
        self._session = session
        self._on_event = on_event
        # Called after the receive loop reconnects following a drop (e.g. a bridge
        # restart), so the coordinator can refresh at once instead of leaving entities
        # unavailable until the next poll.
        self._on_reconnect = on_reconnect
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._recv_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._connect_lock = asyncio.Lock()
        self._closing = False
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

    @property
    def connected(self) -> bool:
        """Whether the WebSocket is open."""
        return self._ws is not None and not self._ws.closed

    async def connect(self) -> None:
        """Open the WebSocket + receive loop (serialized against reconnect)."""
        self._closing = False
        async with self._connect_lock:
            if self.connected:
                return
            try:
                async with asyncio.timeout(15):
                    self._ws = await self._session.ws_connect(self._url, heartbeat=30)
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                msg = f"cannot reach the bridge at {self._url}: {err}"
                raise EufySdkApiClientCommunicationError(msg) from err
            self._recv_task = asyncio.ensure_future(self._receive_loop())

    async def close(self) -> None:
        """Close the WebSocket and stop reconnecting."""
        self._closing = True
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._recv_task:
            self._recv_task.cancel()
            self._recv_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(
                    EufySdkApiClientCommunicationError("connection closed")
                )
        self._pending.clear()

    async def _receive_loop(self) -> None:
        """Read frames: resolve pending requests by id, dispatch events."""
        if self._ws is None:
            return
        try:
            async for msg in self._ws:
                if msg.type is not aiohttp.WSMsgType.TEXT:
                    continue
                data = msg.json()
                mid = data.get("id")
                if mid is not None and mid in self._pending:
                    fut = self._pending.pop(mid)
                    if not fut.done():
                        fut.set_result(data)
                elif data.get("event") and self._on_event:
                    self._on_event(data)
        except (aiohttp.ClientError, asyncio.CancelledError):
            pass
        finally:
            # Clear the socket so `connected` reports the drop, fail in-flight requests,
            # and (unless deliberately closing) reconnect so events resume promptly, not
            # only on the next poll.
            self._ws = None
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(
                        EufySdkApiClientCommunicationError("connection lost")
                    )
            self._pending.clear()
            if not self._closing:
                self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        """Start the reconnect supervisor if it isn't already running."""
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.ensure_future(self._reconnect())

    async def _reconnect(self) -> None:
        """Reopen the WebSocket with capped backoff until closed/connected."""
        delay = 1
        while not self._closing and not self.connected:
            try:
                await self.connect()
            except EufySdkApiClientCommunicationError:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
            else:
                # Back up after a drop — let the coordinator recover entities now, not
                # at the next poll.
                if self._on_reconnect:
                    self._on_reconnect()
                return

    async def rpc(
        self,
        cmd: str,
        timeout: float = 15,  # noqa: ASYNC109 — deliberate per-call timeout API
        **args: Any,
    ) -> dict[str, Any]:
        """Send a command and await its reply. Raises on `ok: false`."""
        if not self.connected:
            msg = "not connected"
            raise EufySdkApiClientCommunicationError(msg)
        self._next_id += 1
        mid = self._next_id
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        await self._ws.send_json({"id": mid, "cmd": cmd, **args})  # type: ignore[union-attr]
        try:
            async with asyncio.timeout(timeout):
                reply = await fut
        except TimeoutError as err:
            self._pending.pop(mid, None)
            msg = f"{cmd}: timed out"
            raise EufySdkApiClientCommunicationError(msg) from err
        if not reply.get("ok"):
            raise EufySdkApiClientError(reply.get("error", f"{cmd} failed"))
        return reply

    # ── auth (mirrors the bridge's auth.* protocol) ──
    async def auth_status(self) -> dict[str, Any]:
        """Return the current auth state (ok|require_2fa|require_captcha|pending)."""
        return (await self.rpc("auth.status"))["auth"]

    async def submit_2fa(self, code: str) -> dict[str, Any]:
        """Submit a 2FA code; returns the new auth state."""
        return (await self.rpc("auth.submit", code=code))["auth"]

    async def submit_captcha(self, answer: str) -> dict[str, Any]:
        """Submit a captcha answer; returns the new auth state."""
        return (await self.rpc("auth.submit", captcha=answer))["auth"]

    async def retrigger_auth(self) -> dict[str, Any]:
        """Request a fresh 2FA code / captcha; returns the new auth state."""
        return (await self.rpc("auth.retrigger"))["auth"]

    # ── devices ──
    async def list_devices(self) -> list[dict[str, Any]]:
        """Every device the bridge exposes (sn/name/model/codec/capabilities/state)."""
        return (await self.rpc("devices.list"))["devices"]

    async def refresh_event_image(self, sn: str) -> bool:
        """Force a 'Last event' image refresh; returns True if a newer image landed."""
        return bool((await self.rpc("event.refresh", sn=sn)).get("changed"))

    async def list_solix_devices(self) -> list[dict[str, Any]]:
        """Return the Anker Solix devices (empty if Solix isn't configured)."""
        # Each: {sn, productCode, name, category, capabilities, values, firmware}.
        return (await self.rpc("solix.devices")).get("devices", [])

    async def set_solix_light(self, sn: str, *, on: bool) -> None:
        """Toggle a Solarbank's ambient light (encrypted set_device_attrs write)."""
        await self.rpc("solix.setLight", deviceSn=sn, on=on)

    async def get_solix_device_attrs(
        self, sn: str, keys: list[str] | None = None
    ) -> dict[str, Any]:
        """Read a Solix device's attributes (e.g. screen_off_time) as a flat map."""
        reply = await self.rpc("solix.getDeviceAttrs", deviceSn=sn, keys=keys or [])
        return reply.get("attributes") or {}

    async def set_solix_display_timeout(self, sn: str, index: int) -> None:
        """Set a Solarbank display screen-off timeout by 1-based index (MQTT)."""
        await self.rpc("solix.setDisplayTimeout", deviceSn=sn, index=index)

    async def get_solix_power_cutoff(
        self, sn: str, site_id: str = ""
    ) -> list[dict[str, Any]]:
        """Read the battery discharge-cutoff (minimum-SOC) preset options."""
        # Each: {id, output_cutoff_data (SOC %), is_selected}.
        reply = await self.rpc("solix.getPowerCutoff", deviceSn=sn, siteId=site_id)
        return reply.get("options") or []

    async def set_solix_power_cutoff(self, sn: str, cutoff_data_id: int) -> None:
        """Select a discharge-cutoff preset by its device id."""
        await self.rpc("solix.setPowerCutoff", deviceSn=sn, cutoffDataId=cutoff_data_id)

    async def get_solix_soc_params(self, sn: str) -> dict[str, Any]:
        """Read a Solarbank's SOC-limit block (discharge/charge limit, reserve)."""
        # {chargeUpperLimit, dischargeLowerLimit, backupReserve, backupReserveSwitch,
        #  socCalibrationEnable} — whole-percent ints, or {} if the site has no block.
        reply = await self.rpc("solix.getSocParams", deviceSn=sn)
        return reply.get("params") or {}

    async def set_solix_soc_limits(
        self,
        sn: str,
        *,
        discharge: int | None = None,
        charge: int | None = None,
    ) -> dict[str, Any]:
        """Write the discharge and/or charge limit (%); read-modify-write keeps rest."""
        kwargs: dict[str, Any] = {"deviceSn": sn}
        if discharge is not None:
            kwargs["dischargeLowerLimit"] = int(discharge)
        if charge is not None:
            kwargs["chargeUpperLimit"] = int(charge)
        reply = await self.rpc("solix.setSocLimits", **kwargs)
        return reply.get("params") or {}

    async def get_properties(self, sn: str) -> list[dict[str, Any]]:
        """Return a device's property manifest (name/type/unit/writable/enumValues)."""
        return (await self.rpc("device.properties", sn=sn))["properties"]

    async def get_config(self) -> dict[str, Any]:
        """Return the bridge's runtime config (currently {pollMs})."""
        return await self.rpc("config.get")

    async def set_poll_ms(self, poll_ms: int) -> int:
        """Set the cloud poll interval (ms); returns the new effective value."""
        return (await self.rpc("config.set", pollMs=poll_ms))["pollMs"]

    async def list_effects(self) -> list[dict[str, Any]]:
        """
        Return the smart-light effect gallery ({id, name, colors}) for effect_list.

        Account-wide and cached by the bridge; the first call enumerates the catalogue
        over several HTTP round-trips, hence the longer timeout.
        """
        reply = await self.rpc("light.effects", timeout=60)
        return reply.get("effects", [])

    async def action(self, sn: str, action: str, *args: Any) -> Any:
        """
        Invoke a capability action (a typed method, not a scalar property) on a device.

        e.g. smart_light `setColor({red,green,blue})` / `setEffect(id)` — controls that
        `set_property` can't reach because they take structured arguments.
        """
        reply = await self.rpc("device.action", sn=sn, action=action, args=list(args))
        return reply.get("result")

    async def preset_slots(self, sn: str) -> list[dict[str, Any]]:
        """
        Read a camera's stored preset positions.

        A P2P request/reply rather than a cloud read, so it answers only while the
        camera is awake — a sleeping battery camera times out. Hence the longer
        timeout, and callers that treat a failure as "ask again later".
        """
        reply = await self.rpc(
            "device.action", timeout=45, sn=sn, action="preset.list", args=[]
        )
        return reply.get("result") or []

    async def set_property(self, sn: str, name: str, value: Any) -> None:
        """Write a device property."""
        await self.rpc("device.set", sn=sn, name=name, value=value)

    async def reboot(self, sn: str) -> None:
        """Reboot a HomeBase (hub-only; it drops offline for a minute or two)."""
        await self.rpc("device.reboot", sn=sn)
