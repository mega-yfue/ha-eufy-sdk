"""Camera platform — a live camera per streaming device, via the bridge's go2rtc."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTRIBUTION,
    CONF_GO2RTC_RTSP_PORT,
    CONF_HOST,
    CONF_PORT,
    DEFAULT_GO2RTC_RTSP_PORT,
    DOMAIN,
)
from .entity import device_offline
from .snapshot_policy import snapshot_url

if TYPE_CHECKING:
    from homeassistant.components.stream import Stream
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a camera for every device the bridge marked with a `stream` path."""
    coordinator = entry.runtime_data.coordinator
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    rtsp_port = int(entry.data.get(CONF_GO2RTC_RTSP_PORT, DEFAULT_GO2RTC_RTSP_PORT))
    async_add_entities(
        EufySdkCamera(coordinator, sn, host, port, rtsp_port)
        for sn, dev in coordinator.data.items()
        if dev.get("stream")
    )


class EufySdkCamera(CoordinatorEntity["EufySdkDataUpdateCoordinator"], Camera):
    """A camera via the bridge: live via go2rtc RTSP, stills via snapshot."""

    _attr_has_entity_name = True
    _attr_name = None  # the device name is the camera name
    _attr_attribution = ATTRIBUTION
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        host: str,
        port: int,
        rtsp_port: int,
    ) -> None:
        """Bind to a device serial + the bridge address."""
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._sn = sn
        self._host = host
        self._port = port
        self._rtsp_port = rtsp_port
        self._attr_unique_id = f"{sn}_camera"
        dev = coordinator.data.get(sn, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, sn)},
            name=dev.get("name") or sn,  # the user's device name (e.g. "Dining room")
            manufacturer="eufy",
            model=dev.get("model") or dev.get("codec"),  # the model code, not the codec
            serial_number=sn,
        )

    @property
    def available(self) -> bool:
        """Available while the bridge still reports this camera and it isn't offline."""
        return (
            super().available
            and self._sn in self.coordinator.data
            and not device_offline(self.coordinator.data.get(self._sn))
        )

    @property
    def _on_battery(self) -> bool:
        """Whether the bridge reports this camera as battery-powered."""
        dev = self.coordinator.data.get(self._sn) or {}
        return "battery" in (dev.get("capabilities") or [])

    async def stream_source(self) -> str:
        """
        Return where the live video comes from.

        A mains camera goes through the bridge's go2rtc RTSP restream. A battery camera
        is pulled straight from the bridge's /stream by Home Assistant's own go2rtc
        (ffmpeg, video copied as-is), so nothing but a live viewer opens it.
        """
        if self._on_battery:
            return (
                f"ffmpeg:http://{self._host}:{self._port}/stream/{self._sn}"
                "#video=copy#async"
            )
        return f"rtsp://{self._host}:{self._rtsp_port}/{self._sn}"

    async def async_create_stream(self) -> Stream | None:
        """
        Skip Home Assistant's stream worker for a battery camera.

        That worker retries a failed or closed source forever, and every retry makes
        the bridge wake the camera over P2P. A battery camera was drained flat that way
        after one live view (ha-eufy-sdk#83). Live view then runs through Home
        Assistant's go2rtc (WebRTC) only, so there is no HLS fallback and no
        camera.record for battery cameras.
        """
        if self._on_battery:
            return None
        return await super().async_create_stream()

    async def async_camera_image(
        self,
        width: int | None = None,  # noqa: ARG002
        height: int | None = None,  # noqa: ARG002
    ) -> bytes | None:
        """Return a still from the bridge's /snapshot endpoint."""
        session = async_get_clientsession(self.hass)
        url = snapshot_url(
            self._host,
            self._port,
            self._sn,
            self.coordinator.config_entry.runtime_data.snapshot_policy.get(self._sn),
        )
        try:
            async with session.get(url, timeout=20) as resp:
                if resp.status == HTTPStatus.OK:
                    return await resp.read()
        except (TimeoutError, OSError):
            return None
        return None
