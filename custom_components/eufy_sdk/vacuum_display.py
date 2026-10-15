"""
Which robot-vacuum properties a RoboVac device page shows, and under which name.

The SDK reports some sixty readings for a RoboVac, and every one of them would
otherwise become an entity. This keeps the page to what an owner checks:

- PRIMARY: status, battery, charging, error, clean mode, suction, mop level and
  dock state, in the device's main area.
- KEPT: consumable hours, run statistics and the everyday settings, under
  Diagnostics.
- Everything else read-only (voice packs, scene and schedule counts, map
  internals, firmware details, rarely used toggles) is created DISABLED, so it can
  still be switched on from the entity list.

The two clean lines name a few of the same facts differently (the legacy Tuya
line's `workStatus` / `cleaningStrength` / `mopWater` against the newer line's
`activity` / `suction` / `mopLevel`). `LABELS` gives each pair one name, so every
RoboVac shows the same rows whichever line it is on.

Writable controls (power, BoostIQ, ...) are left where their platform puts them,
unless they are primary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

VACUUM_CAPABILITY = "vacuum_clean"

# The config-entry minor version from which hidden RoboVac entities are already
# disabled in the registry; an older entry gets the one-time pass below.
DISPLAY_MINOR_VERSION = 2

PRIMARY: frozenset[str] = frozenset(
    {
        "activity",
        "workStatus",
        "battery",
        "chargeState",
        "errorCode",
        "cleanType",
        "suction",
        "cleaningStrength",
        "mopLevel",
        "mopWater",
        "dockState",
    }
)

KEPT: frozenset[str] = frozenset(
    {
        # Consumables
        "sideBrushHours",
        "rollingBrushHours",
        "filterHours",
        "mopHours",
        "dustBagHours",
        # Run statistics
        "clearTime",
        "clearArea",
        "lifetimeCleanTime",
        "lifetimeCleanArea",
        "lifetimeCleanCount",
        # Everyday settings
        "childLock",
        "doNotDisturb",
        "petMode",
        "aiSee",
        "volume",
        "workMode",
        "waterTank",
        "mopPad",
    }
)

LABELS: dict[str, str] = {
    "activity": "Status",
    "workStatus": "Status",
    "chargeState": "Charging",
    "errorCode": "Error",
    "cleaningStrength": "Suction",
    "mopWater": "Mop Level",
    "clearTime": "Last Clean Time",
    "clearArea": "Last Clean Area",
}


def is_vacuum(record: dict | None) -> bool:
    """Whether a bridge device record is a RoboVac."""
    return VACUUM_CAPABILITY in (record or {}).get("capabilities", [])


def hidden(spec: dict[str, Any]) -> bool:
    """Whether a RoboVac property is created disabled: read-only and not listed."""
    name = spec["name"]
    return not spec.get("writable") and name not in PRIMARY and name not in KEPT


def category(spec: dict[str, Any]) -> tuple[bool, EntityCategory | None]:
    """
    Return `(override, category)` for a RoboVac property.

    A primary property sits in the main area whatever its platform; any other
    read-only one goes under Diagnostics. A writable non-primary property keeps the
    category its platform gives it (`override` False).
    """
    if spec["name"] in PRIMARY:
        return True, None
    if not spec.get("writable"):
        return True, EntityCategory.DIAGNOSTIC
    return False, None


@callback
def disable_hidden_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    devices: dict[str, dict],
    properties: dict[str, list[dict[str, Any]]],
) -> None:
    """
    Disable the hidden RoboVac entities an older entry already registered, once.

    `entity_registry_enabled_default` only applies to an entity's first
    registration, so on an existing install every hidden property would stay
    enabled. This pass runs once per entry (gated on its minor version) and only
    touches entities nobody has disabled, so a later re-enable sticks.
    """
    vacuums = [sn for sn, dev in devices.items() if is_vacuum(dev)]
    if not vacuums or entry.minor_version >= DISPLAY_MINOR_VERSION:
        return
    unique_ids = {
        f"{sn}_{spec['name']}"
        for sn in vacuums
        for spec in properties.get(sn, [])
        if hidden(spec)
    }
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.unique_id in unique_ids and entity.disabled_by is None:
            registry.async_update_entity(
                entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )
    # A RoboVac whose properties failed to load is retried on the next setup.
    if all(sn in properties for sn in vacuums):
        hass.config_entries.async_update_entry(
            entry, minor_version=DISPLAY_MINOR_VERSION
        )
