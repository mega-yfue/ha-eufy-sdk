"""
Stored presets — reading them from the camera, and naming them.

Presets are the saved positions; moving the camera by hand is the PTZ d-pad, and
lives in the button platform. The slots belong to the camera, not to us: how many
there are and how they are numbered varies by model, and only the camera knows
which ones hold a position. So the select's options are READ over P2P rather than
generated, and the reading and labelling live here — the select builds the
options, the buttons read them back.

Reading is a P2P request/reply, so it answers only while the camera is awake. A
sleeping battery camera times out instead of returning an empty list, which is
why every read here is best-effort and callers fall back to bare slot numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .const import LOGGER, PRESET_SLOTS

if TYPE_CHECKING:
    from .api import EufySdkApiClient
    from .data import EufySdkConfigEntry

# Moving to a stored position is `preset.preview`, NOT `preset.goto`. Verified on
# hardware: `goto` (wire 6032) reports success and the camera does not move. The
# SDK's own docs explain why — that frame is byte-identical to the save frame, and
# the camera reads it as "store here". `preview` (6035) is a distinct command, the
# one the app uses to swing the camera onto a preset, and it does move it.
# Saving is deliberately absent: presets are created in the eufy app, where you can
# frame the shot while watching it. The SDK does expose a save verb, but it was never
# seen to work here, and a control that quietly does nothing is worse than none.
ACTION_GOTO = "preset.preview"


@dataclass(frozen=True)
class PresetSlot:
    """One stored-position slot, as the camera reports it."""

    index: int
    occupied: bool
    is_default: bool

    @property
    def label(self) -> str:
        """
        Render how the slot reads in the select.

        The default position is the one the camera returns to, so it is named
        rather than numbered — but it keeps its index, since that is what the
        buttons send. An empty slot stays in the list (you need it to save a NEW
        position) and says so, because a go-to against it would otherwise do
        nothing at all, silently.
        """
        if self.is_default:
            return f"{self.index} · HOME"
        return str(self.index) if self.occupied else f"{self.index} · empty"


def fallback_slots() -> list[PresetSlot]:
    """
    Build bare numbered slots, for when the camera could not be asked.

    Marked occupied on purpose: we do not know that they are empty, and blocking
    a go-to on a guess would be worse than letting the camera ignore a frame.
    """
    return [PresetSlot(i, occupied=True, is_default=False) for i in range(PRESET_SLOTS)]


def parse_slots(result: Any) -> list[PresetSlot]:
    """
    Turn a `preset.list` reply into slots.

    Each entry carries the SDK's normalised `id` plus the camera's own `raw`
    reply, whose fields vary by model — commonly `index`, `enable` and
    `isdefault`. Absent flags read as "occupied, not default": the same
    reasoning as `fallback_slots`.
    """
    slots: list[PresetSlot] = []
    for entry in result or []:
        raw = entry.get("raw") or {}
        index = entry.get("id", raw.get("index"))
        if index is None:
            continue
        slots.append(
            PresetSlot(
                index=int(index),
                occupied=raw.get("enable", 1) != 0,
                is_default=bool(raw.get("isdefault", 0)),
            )
        )
    return sorted(slots, key=lambda s: s.index)


async def async_read_slots(
    client: EufySdkApiClient, sn: str
) -> list[PresetSlot] | None:
    """Ask the camera for its slots. None when it could not answer."""
    try:
        return parse_slots(await client.preset_slots(sn))
    except Exception as err:  # noqa: BLE001 — any failure means "ask again later"
        LOGGER.debug("%s: could not read presets (%s)", sn, err)
        return None


async def async_refresh_slots(entry: EufySdkConfigEntry, sn: str) -> list[PresetSlot]:
    """Re-read the slots into runtime data, keeping what we had on failure."""
    runtime = entry.runtime_data
    slots = await async_read_slots(runtime.client, sn)
    if slots:
        runtime.preset_slots[sn] = slots
    return runtime.preset_slots.get(sn) or fallback_slots()


def slots_for(entry: EufySdkConfigEntry, sn: str) -> list[PresetSlot]:
    """Return the slots known for this camera, or bare numbers until a read lands."""
    return entry.runtime_data.preset_slots.get(sn) or fallback_slots()


def remember(entry: EufySdkConfigEntry, sn: str, slots: list[PresetSlot]) -> None:
    """Record slots restored from a previous run, so a cold start is not blind."""
    entry.runtime_data.preset_slots[sn] = slots


def as_dicts(slots: list[PresetSlot]) -> list[dict[str, Any]]:
    """Render slots for the entity state, so a restart can restore them."""
    return [
        {"index": s.index, "occupied": s.occupied, "default": s.is_default}
        for s in slots
    ]


def from_dicts(raw: Any) -> list[PresetSlot]:
    """Rebuild slots from a restored state. Empty when the state carried none."""
    if not isinstance(raw, list):
        return []
    slots = []
    for item in raw:
        if not isinstance(item, dict) or "index" not in item:
            continue
        slots.append(
            PresetSlot(
                index=int(item["index"]),
                occupied=bool(item.get("occupied", True)),
                is_default=bool(item.get("default", False)),
            )
        )
    return slots
