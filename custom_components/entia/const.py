"""Constants for the Entia integration."""

DOMAIN = "entia"

# Seconds to wait for Entia during setup and when validating a login
READY_TIMEOUT = 30

STORAGE_VERSION = 1

# Option: minutes without any change before the connection is closed; 0 = stay connected
CONF_TIMEOUT = "timeout"
DEFAULT_TIMEOUT = 0
MAX_TIMEOUT = 24 * 60

# Device registry entry for the flat; unchanged so existing installs keep their device
DEVICE_NAME = "Entia"
DEVICE_MODEL = "Entia"
DEVICE_MANUFACTURER = "Entia Living"
