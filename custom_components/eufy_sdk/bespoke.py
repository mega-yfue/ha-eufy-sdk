"""
Bespoke handling for known properties the generic classifier can't model well.

Some properties are packed values (`kind: "bitfield"`) that mean nothing as a single
number. When we know the layout we describe it here and the platforms build friendlier
entities (one switch per bit) instead of the generic fallback. Anything not listed
degrades to a read-only diagnostic sensor — add an entry to give a param real controls.
"""

from __future__ import annotations

# Bitfield properties we know how to split into per-bit switches.
# `base` is only a fallback before the device reports a current mask.
# Once state is known, writes preserve that mask and only change the selected bit.
# Different models can carry different enable/vendor bits in the same property.
BITFIELD_SWITCHES: dict[str, dict] = {
    "aiDetectType": {
        "base": 0x30000,
        "bits": {
            "detect_human": 0x2,
            "detect_vehicle": 0x4,
            "detect_pet": 0x8,
            "face_recognition": 0x1,
        },
    },
}
