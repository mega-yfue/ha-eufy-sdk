"""Constants for eufy_sdk."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "eufy_sdk"
ATTRIBUTION = "Data provided by the eufy cloud via ha-eufy-sdk-bridge"

# Config-entry keys: the address of the ha-eufy-sdk-bridge WebSocket.
CONF_HOST = "host"
CONF_PORT = "port"
DEFAULT_PORT = 3000

# Options: how often the bridge polls the cloud for device state (minutes).
# Drives both the bridge's cloud poll (config.set) and how often HA reads it.
CONF_POLL_INTERVAL = "poll_interval_minutes"
DEFAULT_POLL_INTERVAL_MIN = 10

# Schema version this integration targets (the bridge sends its own in `hello`/`ready`).
SUPPORTED_SCHEMA = 1

# How many preset slots to offer before the camera has ever been asked. A last
# resort, not the normal path: the slots are read from the camera (see presets.py)
# and the select remembers the last answer across restarts, so this count is only
# ever seen on the very first start with the camera asleep. The SDK documents this
# many; some models report more, and the first successful read corrects it.
PRESET_SLOTS = 8

# Where the preset select keeps the slots it last read, so they survive a restart.
ATTR_SLOTS = "slots"

# How long to leave between re-reads of a camera's preset slots. Reading is P2P and
# only happens while the camera is already awake for some other reason, so this is
# not a poll — it just stops a long live view from re-asking on every refresh. Long
# enough to be free, short enough that a preset added in the eufy app turns up.
SLOT_REREAD_SECS = 600
