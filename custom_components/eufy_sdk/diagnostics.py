"""
Diagnostics for the eufy_sdk integration.

Downloadable from the device/entry page (Settings → Devices & Services → eufy bridge →
⋮ → Download diagnostics), so a bug report can carry the connection state and the
device inventory without a user hand-copying anything.

Everything that identifies a person, an account, a device or the network is redacted:
device serials (which key the device maps AND appear as values), device names, the
bridge host, Wi-Fi SSIDs, IPs/MACs, the P2P device id, and account/member ids. What
stays is the diagnostic substance — models, codecs, capabilities, and live state — so a
maintainer can see WHY a device behaves oddly without learning WHOSE it is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_HOST

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import EufySdkConfigEntry

# Redacted wherever these keys appear as a VALUE in the dumped structures. Device
# serials, names, the bridge host, network identifiers, and any account/member id
# that can ride a device's state.
TO_REDACT = {
    CONF_HOST,
    "host",
    "serial",
    "serial_number",
    "sn",
    "deviceSn",
    "device_sn",
    "stationSn",
    "name",
    "ssid",
    "ip",
    "ip_address",
    "mac",
    "p2p_did",
    "userId",
    "user_id",
    "adminUserId",
    "shortUserId",
    "account",
    "accountName",
    "email",
    "password",
}


def _redact_device_map(devices: dict[str, dict]) -> dict[str, Any]:
    """
    Pseudonymise a device map's keys and redact each value.

    The maps are keyed by device serial, which `async_redact_data` (a value-level
    redactor) can't reach — so replace the serial keys with stable `device_<n>` tokens
    (sorted for determinism) and redact PII inside each record. Capabilities / model /
    codec / state survive, since those are the point.
    """
    return {
        f"device_{i}": async_redact_data(dev, TO_REDACT)
        for i, (_sn, dev) in enumerate(sorted(devices.items()))
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,  # noqa: ARG001
    entry: EufySdkConfigEntry,
) -> dict[str, Any]:
    """Return redacted diagnostics for one config entry."""
    data = entry.runtime_data
    coordinator = data.coordinator
    client = data.client

    try:
        auth = await client.auth_status()
    except Exception:  # noqa: BLE001 - diagnostics must never raise; report the failure instead
        auth = {"state": "unavailable"}

    devices = coordinator.data or {}
    solix = getattr(coordinator, "solix_devices", {}) or {}

    # A capability tally across all eufy devices — the fastest read on what the
    # account actually has.
    capability_counts: dict[str, int] = {}
    for dev in devices.values():
        for cap in dev.get("capabilities", []) or []:
            capability_counts[cap] = capability_counts.get(cap, 0) + 1

    return {
        "config_entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "connection": {
            "bridge_connected": client.connected,
            "last_update_success": coordinator.last_update_success,
            "auth_state": auth.get("state"),
            "schema_version": auth.get("schemaVersion"),
        },
        "summary": {
            "device_count": len(devices),
            "solix_device_count": len(solix),
            "property_spec_count": sum(len(v) for v in data.properties.values()),
            "capability_counts": capability_counts,
        },
        "devices": _redact_device_map(devices),
        "solix_devices": _redact_device_map(solix),
    }
