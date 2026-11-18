"""
Diagnostics for the eufy_sdk integration.

Downloadable from the device/entry page (Settings → Devices & Services → eufy bridge →
⋮ → Download diagnostics), so a bug report can carry the connection state and the
device inventory without a user hand-copying anything.

Everything that identifies a person, an account, a device or the network is redacted:
device serials (which key the device maps AND appear as values), device names, the
bridge host, Wi-Fi SSIDs/BSSIDs, IPs/MACs, the P2P device id, account/member ids, and
anything credential-shaped. What stays is the diagnostic substance — models, codecs,
capabilities, and live state — so a maintainer can see WHY a device behaves oddly
without learning WHOSE it is.

Redaction is by KEY, case-insensitively and at any depth: a fixed set of identifier
names plus a few substrings (`passwd`/`password`/`account`/`secret`/`token`) so a
future credential- or account-shaped field is covered without being listed. Some
values are base64-wrapped JSON (a HomeBase's `nasSendSecurityPasswd` decodes to an
RTSP username/password; `unknown_1160` decodes to an account id); those are unwrapped,
redacted inside, and re-wrapped so nothing sensitive survives the encoding.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import TYPE_CHECKING, Any

from .const import CONF_HOST

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .data import EufySdkConfigEntry

REDACTED = "**REDACTED**"

# Shortest string worth trying to decode as base64-wrapped JSON — below this it cannot
# hold a meaningful object, so plain text is skipped cheaply.
_MIN_ENCODED_LEN = 16

# Exact key names (matched case-insensitively) whose value is redacted at any depth:
# device serials, names, the bridge host, and network / account / member identifiers.
_REDACT_KEYS = {
    key.lower()
    for key in (
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
        "bssid",
        "ip",
        "ipAddress",
        "ip_address",
        "mac",
        "p2p_did",
        "userId",
        "user_id",
        "adminUserId",
        "shortUserId",
        "email",
    )
}

# Any key CONTAINING one of these (case-insensitive) is redacted too, so a credential-
# or account-shaped field nobody listed — `nasSendSecurityPasswd`, `account_id`, a
# future `*_token` — is still covered.
_REDACT_SUBSTRINGS = ("passwd", "password", "account", "secret", "token")


def _is_sensitive_key(key: Any) -> bool:
    """Whether a value under this key must be redacted."""
    low = str(key).lower()
    return low in _REDACT_KEYS or any(part in low for part in _REDACT_SUBSTRINGS)


def _redact(value: Any) -> Any:
    """
    Recursively redact a diagnostics value.

    A dict's value is replaced with `REDACTED` when its key is sensitive, else walked.
    Lists are walked. A string that is base64-wrapped JSON (e.g. the HomeBase's
    `nasSendSecurityPasswd` / `unknown_1160`) is redacted INSIDE and re-wrapped, so an
    account id or credential buried in an encoded blob does not leak.
    """
    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive_key(key) else _redact(inner)
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_base64_json(value)
    return value


def _redact_base64_json(text: str) -> str:
    """
    Redact a base64-wrapped JSON blob inside, then re-encode it.

    Anything that is not base64-of-JSON — a plain string, an image, an opaque token —
    is returned unchanged, as is a blob that held nothing sensitive (so the dump keeps
    its original bytes rather than churning every encoded field).
    """
    if len(text) < _MIN_ENCODED_LEN or len(text) % 4:
        return text
    try:
        decoded = base64.b64decode(text, validate=True)
        parsed = json.loads(decoded)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return text
    if not isinstance(parsed, (dict, list)):
        return text
    redacted = _redact(parsed)
    if redacted == parsed:
        return text
    cleaned = json.dumps(redacted, separators=(",", ":")).encode()
    return base64.b64encode(cleaned).decode()


def _redact_device_map(devices: dict[str, dict]) -> dict[str, Any]:
    """
    Pseudonymise a device map's keys and redact each value.

    The maps are keyed by device serial, which the value-level redactor can't reach —
    so replace the serial keys with stable `device_<n>` tokens (sorted for determinism)
    and redact PII inside each record. Capabilities / model / codec / state survive,
    since those are the point.
    """
    return {
        f"device_{i}": _redact(dev)
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
        "config_entry": _redact(entry.as_dict()),
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
