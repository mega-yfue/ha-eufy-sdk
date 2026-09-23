"""HomeBase alarm-control-panel entity."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)

from .alarm_logic import (
    MODE_AWAY,
    MODE_CUSTOM_1,
    MODE_CUSTOM_2,
    MODE_CUSTOM_3,
    MODE_DISARMED,
    MODE_HOME,
    alarm_state_for_raw,
)
from .entity import EufySdkDeviceEntity, has_capability

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


class EufySdkAlarmControlPanel(EufySdkDeviceEntity, AlarmControlPanelEntity):
    """Expose verified HomeBase modes through HA's alarm API."""

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

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return the explicit Eufy-to-HA state mapping."""
        state = alarm_state_for_raw(self.device.get("state", {}).get("armingMode"))
        return AlarmControlPanelState(state) if state is not None else None

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
