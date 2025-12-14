"""Inky Display Controller - E-ink display management for Raspberry Pi.

This package provides a daemon that receives MQTT commands from the
picture-display-skill and displays images on an Inky Impression e-ink display.
"""

__version__ = "0.1.0"

from private_assistant_display_controller.config import Settings, load_settings
from private_assistant_display_controller.controller import DisplayController
from private_assistant_display_controller.exceptions import (
    CommunicationError,
    ConfigurationError,
    DisplayControllerError,
    DisplayError,
)
from private_assistant_display_controller.models import (
    DeviceAcknowledge,
    DeviceRegistration,
    DisplayCommand,
    DisplayInfo,
    RegistrationResponse,
)

__all__ = [
    "CommunicationError",
    "ConfigurationError",
    "DeviceAcknowledge",
    "DeviceRegistration",
    "DisplayCommand",
    "DisplayController",
    "DisplayControllerError",
    "DisplayError",
    "DisplayInfo",
    "RegistrationResponse",
    "Settings",
    "__version__",
    "load_settings",
]
