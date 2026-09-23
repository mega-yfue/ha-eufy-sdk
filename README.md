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
- **Lights** — eufy smart lights (on/off, brightness, and RGB colour where supported).
- **Sensors** — battery %, signal, and per-device state.
- **Switches, selects & numbers** — e.g. privacy/enabled, night vision, video/recording quality.
- **Locks** — where the account exposes a supported lock.
- **Robot vacuums & lawn mowers** — recognised and surfaced as sensors, switches, and buttons.
  There's no dedicated HA vacuum/mower card yet (start/dock live as controls, not a robot entity).

**Anker Solix** (power stations / smart meter) is a **separate account** and is **not** in the public
bridge — it ships only in the bridge's `dev`/beta image. With that build, Solix devices appear as
sensors. The eufyMake 3D printer isn't supported yet.

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
