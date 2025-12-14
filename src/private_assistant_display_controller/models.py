"""Pydantic models for MQTT message schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class DisplayInfo(BaseModel):
    """Display hardware information sent during device registration."""

    width: int = Field(default=1600, description="Display width in pixels")
    height: int = Field(default=1200, description="Display height in pixels")
    orientation: Literal["landscape", "portrait"] = Field(default="landscape", description="Display orientation")
    model: str = Field(default="inky_impression_13_spectra6", description="Display model identifier")


class DeviceRegistration(BaseModel):
    """Device registration payload sent on startup.

    Published to topic: inky/register
    """

    device_id: str = Field(description="Unique device identifier")
    display: DisplayInfo = Field(default_factory=DisplayInfo, description="Display hardware characteristics")
    room: str | None = Field(default=None, description="Room where device is located")


class RegistrationResponse(BaseModel):
    """Response received after successful device registration.

    Received on topic: inky/{device_id}/registered
    """

    status: Literal["registered", "updated"] = Field(description="Registration result")
    minio_endpoint: str = Field(description="MinIO server endpoint")
    minio_bucket: str = Field(description="Bucket containing images")
    minio_access_key: str = Field(description="Read-only access key")
    minio_secret_key: str = Field(description="Read-only secret key")
    minio_secure: bool = Field(default=False, description="Use HTTPS for MinIO")


class DisplayCommand(BaseModel):
    """Command received from MQTT to control the display.

    Received on topic: inky/{device_id}/command
    """

    action: Literal["display", "clear", "status"] = Field(description="Command action")
    image_path: str | None = Field(default=None, description="MinIO object path for display action")
    image_id: str | None = Field(default=None, description="Image UUID for tracking")
    title: str | None = Field(default=None, description="Image title for device logging")


class DeviceAcknowledge(BaseModel):
    """Acknowledgment sent after processing a command.

    Published to topic: inky/{device_id}/status
    """

    device_id: str = Field(description="Device identifier")
    image_id: str | None = Field(default=None, description="Currently displayed image UUID")
    successful_display_change: bool = Field(description="Whether the display change was successful")
    error: str | None = Field(default=None, description="Error message if any")
