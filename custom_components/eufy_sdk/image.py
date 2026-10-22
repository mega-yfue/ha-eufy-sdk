"""Image platform — per camera: the last event thumbnail and the last screen."""

from __future__ import annotations

import asyncio
import hashlib
from http import HTTPStatus
from typing import TYPE_CHECKING

import aiohttp
from homeassistant.components.image import ImageEntity
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import CONF_HOST, CONF_PORT, EVENT_TYPE
from .entity import EufySdkDeviceEntity
from .pushmap import EVENT_IMAGE_REFRESH, LAST_SCREEN_REFRESH, THUMBNAIL_EVENTS

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry

# Bridge device events are re-fired on the HA bus under this type (see __init__.py).


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create Last event and Last screen images for every camera the bridge exposes."""
    coordinator = entry.runtime_data.coordinator
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    cameras = [sn for sn, dev in coordinator.data.items() if dev.get("stream")]
    async_add_entities(
        [EufySdkEventImage(hass, coordinator, sn, host, port) for sn in cameras]
        + [EufySdkLastScreenImage(hass, coordinator, sn, host, port) for sn in cameras]
    )


class EufySdkEventImage(EufySdkDeviceEntity, ImageEntity):
    """
    Latest-detection-event thumbnail for one device.

    The SDK downloads and retains each event's thumbnail; the bridge serves the
    retained bytes at `/event-image/<sn>`. This entity fetches from the bridge (never
    the raw cloud URL — that needs the SDK's auth/decode). It refreshes on a
    detection push for the device (events that carry a new thumbnail — not
    telemetry like ptzNotify) and on the coordinator poll, stamping a new
    `image_last_updated` only when the bytes change — so an already-retained
    thumbnail shows even before the first event.
    """

    _attr_translation_key = "last_event"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        host: str,
        port: int,
    ) -> None:
        """Bind to a device serial + the bridge address."""
        EufySdkDeviceEntity.__init__(self, coordinator, sn)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{sn}_last_event"
        self._url = f"http://{host}:{port}/event-image/{sn}"
        self._image: bytes | None = None
        self._hash: str | None = None
        # A detection push and the eventImageUpdated nudge both trigger a refresh.
        # Without this the two fetches race: an earlier fetch of the OLD bytes can
        # finish last and overwrite the new image while stamping a fresh time (time
        # moves, picture stale). Serialise so the last write is always the newest fetch.
        self._refresh_lock = asyncio.Lock()

    async def async_added_to_hass(self) -> None:
        """Subscribe to bridge events and pull any already-retained thumbnail."""
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_TYPE, self._handle_event))
        await self._refresh()

    @callback
    def _handle_event(self, event: Event) -> None:
        """
        Re-pull the thumbnail on a detection or the bridge's image-refresh nudge.

        Fires on a detection event for this device, or on the bridge's
        `eventImageUpdated` nudge once it has a fresh local cover. Telemetry/state
        events the bridge also forwards (ptzNotify, batteryLevel, armingModeChanged,
        …) carry no new thumbnail, so they must not stamp a new "Last event" or spam
        the bridge with refetches. The coordinator poll (`_handle_coordinator_update`)
        still catches any thumbnail no event announced.
        """
        data = event.data
        if data.get("deviceSn") != self._sn:
            return
        ev = data.get("event")
        if ev not in THUMBNAIL_EVENTS and ev != EVENT_IMAGE_REFRESH:
            return
        self.hass.async_create_task(self._refresh())

    @callback
    def _handle_coordinator_update(self) -> None:
        """Piggyback the poll cadence to catch a thumbnail no event announced."""
        self.hass.async_create_task(self._refresh())
        super()._handle_coordinator_update()

    async def _refresh(self) -> None:
        """
        Fetch the retained thumbnail; stamp a new time only when the bytes change.

        Serialised: concurrent refreshes (detection + nudge) must not interleave, or an
        old-bytes fetch that finishes last would clobber the new one.
        """
        async with self._refresh_lock:
            data = await self._fetch()
            if data is None:
                return
            digest = hashlib.sha1(data, usedforsecurity=False).hexdigest()
            if digest == self._hash:
                return
            self._image = data
            self._hash = digest
            self._attr_image_last_updated = dt_util.utcnow()
            self.async_write_ha_state()

    async def _fetch(self) -> bytes | None:
        """GET the retained thumbnail bytes from the bridge, or None if unavailable."""
        session = async_get_clientsession(self.hass)
        try:
            resp = await session.get(self._url, timeout=aiohttp.ClientTimeout(total=15))
            if resp.status != HTTPStatus.OK:
                return None
            return await resp.read()
        except (aiohttp.ClientError, TimeoutError):
            return None

    async def async_image(self) -> bytes | None:
        """Return the last thumbnail we fetched (HA's image proxy calls this)."""
        return self._image


class EufySdkLastScreenImage(EufySdkEventImage):
    """
    The last picture someone watched live on this camera ("Last screen").

    Kept by the bridge while a live stream runs (~5 s in, every 30 s, at the end)
    and served at `/last-screen/<sn>` — apart from "Last event", so a detection
    never replaces what was last seen. Refreshes on the bridge's
    `lastScreenUpdated` nudge only (after live viewing or the "Take screenshot"
    button) — never on the coordinator poll, so nothing refreshes it on a timer
    and a battery camera is never woken for it. `image_last_updated` is when the
    picture was taken (`x-saved-at`), not when HA fetched it. Unavailable image
    (404) until the camera was watched or screenshotted once.
    """

    _attr_translation_key = "last_screen"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        host: str,
        port: int,
    ) -> None:
        """Bind to a device serial + the bridge address."""
        super().__init__(hass, coordinator, sn, host, port)
        self._attr_unique_id = f"{sn}_last_screen"
        self._url = f"http://{host}:{port}/last-screen/{sn}"
        self._saved_at = None

    @callback
    def _handle_event(self, event: Event) -> None:
        """Re-pull only on the bridge's last-screen nudge for this camera."""
        data = event.data
        if (
            data.get("deviceSn") == self._sn
            and data.get("event") == LAST_SCREEN_REFRESH
        ):
            self.hass.async_create_task(self._refresh())

    @callback
    def _handle_coordinator_update(self) -> None:
        """Only write state — the last screen changes on its nudge, not on the poll."""
        EufySdkDeviceEntity._handle_coordinator_update(self)  # noqa: SLF001

    async def _refresh(self) -> None:
        """Fetch the last screen; on new bytes stamp the time the picture was taken."""
        async with self._refresh_lock:
            data = await self._fetch()
            if data is None:
                return
            digest = hashlib.sha1(data, usedforsecurity=False).hexdigest()
            if digest == self._hash:
                return
            self._image = data
            self._hash = digest
            self._attr_image_last_updated = self._saved_at or dt_util.utcnow()
            self.async_write_ha_state()

    async def _fetch(self) -> bytes | None:
        """GET the last screen, remembering `x-saved-at`; None if there is none yet."""
        session = async_get_clientsession(self.hass)
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            resp = await session.get(self._url, timeout=timeout)
            if resp.status != HTTPStatus.OK:
                return None
            self._saved_at = dt_util.parse_datetime(
                resp.headers.get("x-saved-at", "") or ""
            )
            return await resp.read()
        except (aiohttp.ClientError, TimeoutError):
            return None
