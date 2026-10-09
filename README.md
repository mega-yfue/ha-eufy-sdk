# ha-eufy-sdk

[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5?logo=home-assistant&logoColor=white)](https://hacs.xyz)
[![Validate](https://github.com/mega-yfue/ha-eufy-sdk/actions/workflows/validate.yml/badge.svg)](https://github.com/mega-yfue/ha-eufy-sdk/actions/workflows/validate.yml)
[![Lint](https://github.com/mega-yfue/ha-eufy-sdk/actions/workflows/lint.yml/badge.svg)](https://github.com/mega-yfue/ha-eufy-sdk/actions/workflows/lint.yml)
[![release](https://img.shields.io/github/v/release/mega-yfue/ha-eufy-sdk?sort=semver)](https://github.com/mega-yfue/ha-eufy-sdk/releases)
[![license](https://img.shields.io/github/license/mega-yfue/ha-eufy-sdk)](./LICENSE)

The Home Assistant integration for eufy — installed via **HACS**. This is the front door: it talks to
the [`ha-eufy-sdk-bridge`](https://github.com/mega-yfue/ha-eufy-sdk-bridge) over WebSocket and turns
every device the bridge reports into HA entities, with live video via the bridge's bundled go2rtc.

- **Config flow**: point it at a bridge URL, or let it auto-discover the add-on via the Supervisor.
- **Entities** are derived from each device's `capabilities` — no per-device Python.
- **Video** uses `stream_source()` → go2rtc → WebRTC, so a camera streams with nothing extra installed.

## Requirements

This integration is a **client**. It does nothing on its own — it needs the
[`ha-eufy-sdk-bridge`](https://github.com/mega-yfue/ha-eufy-sdk-bridge) running and logged into your
eufy account (the bridge is where the eufy login, device list, and video actually live). Set that up
**first**: run it as a Docker container next to Home Assistant, or install the
[add-on](https://github.com/mega-yfue/ha-eufy-sdk-addon). Home Assistant **2026.6.4+**.

## Install

**1. Add the repository to HACS** — one click:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mega-yfue&repository=ha-eufy-sdk&category=integration)

Or manually: HACS → **Custom repositories** → add this repo as an *Integration* → **Install**.
Restart Home Assistant when HACS asks.

**2. Add the integration** — **Settings → Devices & Services → Add Integration → eufy-sdk**.
The config flow asks for your bridge's host and port (or auto-discovers the add-on via the
Supervisor). If eufy needs **2FA or a captcha** on first login, the flow walks you through it in the
UI. Once it connects, your devices show up as entities automatically.

## What you get

Entities are built from what each device reports, so you only get what your hardware supports:

- **Cameras & doorbells** — live WebRTC/HLS video (via go2rtc), snapshots, and a "Last event" image.
- **Events** — motion / person / pet / package / doorbell-ring, as HA events + triggers.
- **Doorbell sensors** — a *Ringing* binary sensor (on at a press, clears after 30 s) and a
  *Package* binary sensor (on at delivery, stays on if stranded, off when taken; survives restarts).
- **Lights** — eufy smart lights (on/off, brightness, and RGB colour where supported).
- **Sensors** — battery %, signal, and per-device state.
- **Switches, selects & numbers** — e.g. privacy/enabled, night vision, video/recording quality.
- **Locks** — where the account exposes a supported lock.
- **Robot vacuums & lawn mowers** — recognised and surfaced as sensors, switches, and buttons.
  There's no dedicated HA vacuum/mower card yet (start/dock live as controls, not a robot entity).

**Anker Solix** (power stations / smart meter) is a **separate account** and is **not** in the public
bridge — it ships only in the bridge's `dev`/beta image. With that build, Solix devices appear as
sensors. The eufyMake 3D printer isn't supported yet.

## Snapshot policy

The Camera entities expose a per-camera **Snapshot policy** select that controls how
still images are requested from the bridge:

- **Default**: use the bridge's configured snapshot behaviour.
- **Auto**: use the bridge's automatic battery-capability policy.
- **Stored**: use retained or persisted imagery without starting live acquisition.
- **Live**: try live acquisition first, with retained or persisted imagery available
  as fallback.

Default works with the bare snapshot endpoint. Auto, Stored, and Live require a bridge version
containing [request-mode support](https://github.com/mega-yfue/ha-eufy-sdk-bridge/pull/79). Older
bridge versions ignore the `mode` query and therefore use Default behaviour.

## Migrating from `fuatakgun/eufy_security`

Two things that cost real time when moving an existing setup across. Neither is a bug — they are
just not guessable from either side.

**Entity IDs are renamed.** The suffix changes are consistent, so a search-and-replace does most
of the work once you know them:

| Old (`eufy_security`) | New (`eufy_sdk`) |
| --- | --- |
| `binary_sensor.X_person_detected` | `binary_sensor.X_person` |
| `binary_sensor.X_motion_detected` | `binary_sensor.X_motion` |
| `image.X_event_image` | `image.X_last_event` |
| `select.X_guard_mode` | `select.X_arming_mode` |

On a 31-device setup that came to 263 substitutions across 10 files — automations, scripts,
templates, packages and dashboards. Worth knowing: YAML-mode dashboards under `config/lovelace/`
are not in `.storage`, so a sweep that only reads the entity registry will miss them.

**Set mode vs current mode.** `select.X_arming_mode` is the mode the station is *set* to, so
on Schedule it reads `schedule`. The mode it is enforcing right now is `sensor.X_current_mode`,
resolved from the station's timetable in Home Assistant's configured time zone. That has to match
the station's local time: with HA left on UTC, every slot resolves shifted by the offset. On Geo it
reads unknown, because the hub doesn't report which mode presence chose.

**The two integrations keep separate device registries.** The same physical camera gets its own
device entry under each integration, so a migration script that maps old entities to new ones by
`device_id` finds nothing at all. Match devices by **name** instead, then translate the entity
suffixes above.

## Where it fits

| Repo | Role |
| --- | --- |
| [`eufy-sdk`](https://github.com/mega-yfue/eufy-sdk) | the HA-agnostic library |
| [`ha-eufy-sdk-bridge`](https://github.com/mega-yfue/ha-eufy-sdk-bridge) | WS + HTTP + go2rtc daemon (Docker) |
| [`ha-eufy-sdk-addon`](https://github.com/mega-yfue/ha-eufy-sdk-addon) | Home Assistant add-on wrapper |
| **`ha-eufy-sdk`** | **this** — the HACS integration (front door) |

## Contributing

Contributions are welcome — please branch from **`dev`** and open your PR against **`dev`** (not
`main`). See [CONTRIBUTING.md](./CONTRIBUTING.md) for the branch model, CI checks, and how releases
are cut.

### Decoded property readings

With a bridge implementing [decoded readings](https://github.com/mega-yfue/ha-eufy-sdk-bridge/pull/85),
the client uses the SDK's namespaced getter values for mapped properties. For example,
recording quality can arrive as a structured raw configuration while the SDK getter
returns the active tier; the select then shows the tier's label instead of `unknown`.
Entity property identities and the `device.set` write route stay the same. This
addresses one source of unknown settings, not every device or connectivity issue.

Durations declared as decoded kind `seconds` are interpreted as numbers, even
when the stored type is a string. Writable durations appear as Number controls;
read-only durations appear as numeric Sensors. Missing or invalid decoded
durations remain unknown.

The client fetches `device.properties` metadata once per device and reuses it for
setup and later polls. It refetches after a connection change or `ready` event, or
when a device's model, capabilities, or decoded accessor keys change. Metadata
requests are serialized and concurrent callers share cached results. Failed metadata
requests and stale-session responses still fail that coordinator refresh. If a model
change adds or removes entities, reload the integration to rebuild those entities.

During decoded snapshot refreshes, each metadata capability must identify its
accessor and provide a read list; each read must identify both its getter accessor
and flat property name. Malformed or missing decoded metadata discards only that
device's cached metadata and preserves its unchanged raw snapshot; other devices
continue using their decoded readings. A warning is logged once per affected
device until its metadata is repaired or the device is removed. The next ordinary
refresh requests that device's metadata again, without resetting the connection
or scheduling a special retry. Empty read lists and valid unmatched reads are
allowed: metadata and values arrive separately, and getter names can differ from
flat property names. The client does not guess a missing alias or clear unrelated
raw properties. Metadata matching an absent snapshot reading produces unknown.

With valid metadata, decoded readings are authoritative: missing, null, invalid,
or ambiguous mapped readings become unknown even when the raw property contains
a scalar. Raw and decoded scalars need not have the same polarity, units, or
validation. Valid decoded values such as `false` and `0` are preserved. Unrelated raw
properties and write-only settings retain their existing behavior. Older bridges
without `decodedState` continue through the legacy raw-state path. This does not
add a decoded event stream, increase the configured polling frequency, or guarantee
that a snapshot is a fresh physical device confirmation.
