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
from .preload import async_check_preload, on_battery_power
from .snapshot_policy import snapshot_url

if TYPE_CHECKING:
    from typing import Any

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
    """Create cameras for the bridge's advertised optical views or legacy stream."""
    coordinator = entry.runtime_data.coordinator
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    rtsp_port = int(entry.data.get(CONF_GO2RTC_RTSP_PORT, DEFAULT_GO2RTC_RTSP_PORT))
    async_add_entities(
        EufySdkCamera(coordinator, sn, host, port, rtsp_port, view=view)
        for sn, dev in coordinator.data.items()
        if dev.get("stream")
        for view in dev.get("views", [None])
    )


class EufySdkCamera(CoordinatorEntity["EufySdkDataUpdateCoordinator"], Camera):
    """A camera via the bridge: live via go2rtc RTSP, stills via snapshot."""

    _attr_has_entity_name = True
    _attr_name = None  # the device name is the camera name
    _attr_attribution = ATTRIBUTION
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(  # noqa: PLR0913 - retain the existing address arguments and add view selection
        self,
        coordinator: EufySdkDataUpdateCoordinator,
        sn: str,
        host: str,
        port: int,
        rtsp_port: int,
        *,
        view: dict[str, Any] | None = None,
    ) -> None:
        """Bind to a device serial + the bridge address."""
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._sn = sn
        self._host = host
        self._port = port
        self._rtsp_port = rtsp_port
        self._view = view["view"] if view else 0
        self._stream_id = view["id"] if view else sn
        self._snapshot_path = view["snapshot"] if view else None
        self._attr_unique_id = (
            f"{sn}_camera" if self._view == 0 else f"{sn}_camera_view_{self._view}"
        )
        if self._view != 0:
            self._attr_name = f"View {self._view}"
        dev = coordinator.data.get(sn, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, sn)},
            name=dev.get("name") or sn,  # the user's device name (e.g. "Dining room")
            manufacturer="eufy",
            model=dev.get("model") or dev.get("codec"),  # the model code, not the codec
            serial_number=sn,
        )

    async def async_added_to_hass(self) -> None:
        """Flag stream preloading on a battery camera once the entity is registered."""
        await super().async_added_to_hass()
        await async_check_preload(
            self.hass, self.entity_id, self._sn, self.coordinator.data.get(self._sn)
        )

    @property
    def available(self) -> bool:
        """Available while the bridge still reports this camera and it isn't offline."""
        return (
            super().available
            and self._sn in self.coordinator.data
            and not device_offline(self.coordinator.data.get(self._sn))
            and (
                self._view == 0
                or any(
                    view["view"] == self._view
                    for view in self.coordinator.data[self._sn].get("views", [])
                )
            )
        )

    async def stream_source(self) -> str:
        """
        Return the go2rtc source for this camera.

        Wired cameras keep a plain RTSP URL, which Home Assistant's stream worker
        can open. A battery camera skips that worker, so live view is only Home
        Assistant's go2rtc reading this URL. The bridge does not answer DESCRIBE
        until the camera wakes and sends video, often 10-30 s, and go2rtc's RTSP
        client gives DESCRIBE a hard-coded 5 s. An ffmpeg source waits longer to
        start and applies a 40 s RTSP timeout (ha-eufy-sdk#153).
        """
        url = f"rtsp://{self._host}:{self._rtsp_port}/{self._stream_id}"
        if on_battery_power(self.coordinator.data.get(self._sn)):
            return f"ffmpeg:{url}#video=copy#timeout=40"
        return url

    async def async_create_stream(self) -> Stream | None:
        """
        Skip Home Assistant's stream worker for a battery camera.

        That worker retries a failed or closed source forever, and every retry makes
        the bridge wake the camera over P2P. A battery camera was drained flat that way
        after one live view (ha-eufy-sdk#83). Live view then runs through Home
        Assistant's go2rtc (WebRTC) on the same RTSP restream, so there is no HLS
        fallback and no camera.record for battery cameras.
        """
        if on_battery_power(self.coordinator.data.get(self._sn)):
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
            snapshot_path=self._snapshot_path,
        )
        try:
            async with session.get(url, timeout=20) as resp:
                if resp.status == HTTPStatus.OK:
                    return await resp.read()
        except (TimeoutError, OSError):
            return None
        return None
