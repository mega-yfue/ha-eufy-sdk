"""HomeBase alarm-control-panel entity."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.util import dt as dt_util

from .alarm_logic import (
    MODE_AWAY,
    MODE_CUSTOM_1,
    MODE_CUSTOM_2,
    MODE_CUSTOM_3,
    MODE_DISARMED,
    MODE_HOME,
    panel_state_for,
)
from .entity import EufySdkDeviceEntity, ScheduleBoundaryMixin, has_capability
from .schedule_logic import current_mode_attributes, current_mode_for

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import EufySdkDataUpdateCoordinator
    from .data import EufySdkConfigEntry


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EufySdkConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one alarm panel for each device declaring arming capability."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        EufySdkAlarmControlPanel(coordinator, serial)
        for serial, device in coordinator.data.items()
        if has_capability(device, "arming")
    )


class EufySdkAlarmControlPanel(
    ScheduleBoundaryMixin, EufySdkDeviceEntity, AlarmControlPanelEntity
):
    """
    Expose verified HomeBase modes through HA's alarm API.

    On Schedule the panel shows the mode the timetable enforces right now (the same
    resolution as the Current mode sensor), re-evaluated at each slot boundary; on
    Geo, which only the hub can resolve, it stays unknown.
    """

    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS
        | AlarmControlPanelEntityFeature.ARM_NIGHT
        | AlarmControlPanelEntityFeature.ARM_VACATION
    )

    def __init__(self, coordinator: EufySdkDataUpdateCoordinator, serial: str) -> None:
        """Bind the entity to one capability-advertising device."""
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_alarm_control_panel"
        self._attr_name = "Security mode"
        self._boundary_unsub = None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """
        The explicit Eufy-to-HA mapping, with the alarm lifecycle layered on top.

        `triggered` while the hub reports its alarm sounding and `pending` during an
        entry/exit delay — both come from the `alarm` push (see alarm_sync) and clear on
        the hub's own stop push or the auto-clear fallback.
        """
        alarms = self.coordinator.config_entry.runtime_data.station_alarms
        mode, _source = current_mode_for(self.device.get("state", {}), dt_util.now())
        state = panel_state_for({"armingMode": mode}, alarms.get(self._sn))
        return AlarmControlPanelState(state) if state is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The set mode, the enforced mode id and its source, as on Current mode."""
        return current_mode_attributes(self.device.get("state", {}), dt_util.now())

    async def _set_mode(self, raw: int) -> None:
        """Send a raw mode; the bridge event is the canonical state update."""
        client = self.client
        await client.set_property(self._sn, "armingMode", raw)

    async def async_alarm_arm_home(
        self,
        code: str | None = None,  # noqa: ARG002
    ) -> None:
        """Arm home; this SDK path has no PIN/code parameter."""
        await self._set_mode(MODE_HOME)

    async def async_alarm_arm_away(
        self,
        code: str | None = None,  # noqa: ARG002
    ) -> None:
        """Arm away; this SDK path has no PIN/code parameter."""
        await self._set_mode(MODE_AWAY)

    async def async_alarm_disarm(
        self,
        code: str | None = None,  # noqa: ARG002
    ) -> None:
        """Disarm; this SDK path has no PIN/code parameter."""
        await self._set_mode(MODE_DISARMED)

    async def async_alarm_arm_custom_bypass(
        self,
        code: str | None = None,  # noqa: ARG002
    ) -> None:
        """Arm custom 1; this SDK path has no PIN/code parameter."""
        await self._set_mode(MODE_CUSTOM_1)

    async def async_alarm_arm_night(
        self,
        code: str | None = None,  # noqa: ARG002
    ) -> None:
        """Arm custom 2; this SDK path has no PIN/code parameter."""
        await self._set_mode(MODE_CUSTOM_2)

    async def async_alarm_arm_vacation(
        self,
        code: str | None = None,  # noqa: ARG002
    ) -> None:
        """Arm custom 3; this SDK path has no PIN/code parameter."""
        await self._set_mode(MODE_CUSTOM_3)
