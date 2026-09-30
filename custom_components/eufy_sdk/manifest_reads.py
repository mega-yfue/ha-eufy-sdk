"""Adapt namespaced SDK reads to HA's existing property identities."""

from __future__ import annotations

from math import isfinite
from typing import Any


def valid_read_metadata(manifest: Any) -> bool:
    """Require usable descriptor identities without requiring matching snapshot keys."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("details"), list):
        return False
    for capability in manifest["details"]:
        if not isinstance(capability, dict):
            return False
        namespace = capability.get("accessor")
        reads = capability.get("reads")
        if (
            not isinstance(namespace, str)
            or not namespace
            or not isinstance(reads, list)
        ):
            return False
        for read in reads:
            if not isinstance(read, dict):
                return False
            name, accessor = read.get("property"), read.get("accessor")
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(accessor, str)
                or not accessor
            ):
                return False
    return True


def _reads(manifest: Any) -> dict[str, tuple[str, dict] | None]:
    """Index flat property names, refusing ambiguous capability mappings."""
    result: dict[str, tuple[str, dict] | None] = {}
    if not isinstance(manifest, dict) or not isinstance(manifest.get("details"), list):
        return result
    for capability in manifest["details"]:
        if not isinstance(capability, dict):
            continue
        namespace = capability.get("accessor")
        reads = capability.get("reads")
        if not isinstance(namespace, str) or not isinstance(reads, list):
            continue
        for read in reads:
            if not isinstance(read, dict):
                continue
            name, accessor = read.get("property"), read.get("accessor")
            if not isinstance(name, str) or not isinstance(accessor, str):
                continue
            result[name] = None if name in result else (namespace, read)
    return result


def _number(value: Any) -> bool:
    """Whether a JSON value is a finite number, excluding booleans."""
    if type(value) not in (int, float):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def _decoded_type(read: dict) -> str | None:
    """Use the decoded enum domain before a structured storage type."""
    values = read.get("values")
    if read.get("kind") == "enum" and isinstance(values, list) and values:
        if all(_number(value) for value in values):
            return "number"
        if all(isinstance(value, str) for value in values):
            return "string"
        return None
    return "number" if read.get("type") == "enum" else read.get("type")


def _value(read: dict, value: Any) -> Any:
    """Keep false/zero, but never publish missing or malformed reads as data."""
    value_type = _decoded_type(read)
    valid = (
        (value_type == "bool" and isinstance(value, bool))
        or (value_type == "number" and _number(value))
        or (value_type == "string" and isinstance(value, str))
    )
    if not valid:
        return None
    values = read.get("values")
    if isinstance(values, list) and not any(
        (type(option) is type(value) and option == value)
        or (_number(option) and _number(value) and option == value)
        for option in values
    ):
        return None
    return value


def snapshot_signature(device: dict[str, Any]) -> tuple:
    """Identify changes that require a fresh manifest, excluding scalar values."""
    decoded = device.get("decodedState")
    shape = (
        tuple(
            sorted(
                (namespace, tuple(sorted(surface)) if isinstance(surface, dict) else ())
                for namespace, surface in decoded.items()
            )
        )
        if isinstance(decoded, dict)
        else ()
    )
    return (
        device.get("model"),
        device.get("modelName"),
        device.get("codec"),
        tuple(sorted(device.get("capabilities") or [])),
        shape,
    )


def normalize_snapshot(
    device: dict[str, Any], metadata: dict[str, Any]
) -> dict[str, Any]:
    """Project decoded reads while preserving unrelated legacy properties."""
    if "decodedState" not in device:
        return device
    manifest = metadata.get("decodedProperties")
    if not isinstance(manifest, dict):
        return device
    reads = _reads(manifest)
    decoded = device.get("decodedState")
    raw = device.get("state")
    state = dict(raw) if isinstance(raw, dict) else {}
    for name, mapping in reads.items():
        state[name] = None
        if mapping is None or not isinstance(decoded, dict):
            continue
        namespace, read = mapping
        surface = decoded.get(namespace)
        if isinstance(surface, dict):
            state[name] = _value(read, surface.get(read["accessor"]))
    return {**device, "state": state}


def normalize_properties(reply: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep property IDs and write-only controls while adapting decoded metadata."""
    properties = reply["properties"]
    if "decodedProperties" not in reply:
        return properties
    reads = _reads(reply["decodedProperties"])
    result = []
    names = set()
    for original in properties:
        name = original["name"]
        names.add(name)
        spec = dict(original)
        mapping = reads.get(name)
        if mapping is not None:
            _, read = mapping
            spec.update(_read_spec(read))
            # A declared public getter can decode a raw structured storage value.
            spec.pop("raw", None)
            spec.pop("unexposed", None)
        elif name in reads:
            # A flat entity cannot identify which namespaced setter was intended.
            spec["writable"] = False
        result.append(spec)
    for name, mapping in reads.items():
        if name not in names and mapping is not None:
            result.append({"name": name, **_read_spec(mapping[1])})
    return result


def _read_spec(read: dict) -> dict[str, Any]:
    """Translate the existing manifest's read metadata to HA's property schema."""
    labels = read.get("labels")
    values = read.get("values")
    enum_values = None
    if isinstance(values, list):
        enum_values = {
            str(value): str(labels.get(str(value), value))
            if isinstance(labels, dict)
            else str(value)
            for value in values
        }
    return {
        "type": _decoded_type(read),
        "kind": read.get("kind"),
        "unit": read.get("unit"),
        "writable": read.get("writable") is True,
        "enumValues": enum_values,
        "description": read.get("description"),
    }
