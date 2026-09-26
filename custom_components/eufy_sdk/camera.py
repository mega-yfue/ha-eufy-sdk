"""Camera platform — a live camera per streaming device, via the bridge's go2rtc."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONF_HOST, CONF_PORT, DOMAIN
from .entity import device_offline

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry

# go2rtc (bundled in the bridge) serves RTSP here; its stream id is the device serial.
GO2RTC_RTSP_PORT = 8554


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a camera for every device the bridge marked with a `stream` path."""
    coordinator = entry.runtime_data.coordinator
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    async_add_entities(
        EufySdkCamera(coordinator, sn, host, port)
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
    ) -> None:
        """Bind to a device serial + the bridge address."""
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._sn = sn
        self._host = host
        self._port = port
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

    async def stream_source(self) -> str:
        """Return the go2rtc RTSP URL — HA's stream component + go2rtc do the work."""
        return f"rtsp://{self._host}:{GO2RTC_RTSP_PORT}/{self._sn}"

    async def async_camera_image(
        self,
        width: int | None = None,  # noqa: ARG002
        height: int | None = None,  # noqa: ARG002
    ) -> bytes | None:
        """Return a still from the bridge's /snapshot endpoint."""
        session = async_get_clientsession(self.hass)
        url = f"http://{self._host}:{self._port}/snapshot/{self._sn}"
        try:
            async with session.get(url, timeout=20) as resp:
                if resp.status == HTTPStatus.OK:
                    return await resp.read()
        except (TimeoutError, OSError):
            return None
        return None
