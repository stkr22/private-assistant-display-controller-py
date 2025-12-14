"""Tests for the DisplayController."""

import pytest
from PIL import Image

from private_assistant_display_controller.display import MockDisplay
from private_assistant_display_controller.exceptions import DisplayError
from private_assistant_display_controller.models import (
    DisplayCommand,
    RegistrationResponse,
)


class TestMockDisplay:
    """Tests for the MockDisplay implementation."""

    @pytest.mark.asyncio
    async def test_show_image(self, sample_image: Image.Image) -> None:
        """Test that MockDisplay stores the displayed image."""
        display = MockDisplay()
        await display.show_image(sample_image)

        assert display.last_image is not None
        assert display.last_image.size == sample_image.size
        assert display.display_count == 1

    @pytest.mark.asyncio
    async def test_clear(self) -> None:
        """Test that clear removes the stored image."""
        display = MockDisplay()
        await display.show_image(Image.new("RGB", (1600, 1200), "red"))
        assert display.last_image is not None

        await display.clear()
        assert display.last_image is None
        assert display.display_count == 2

    @pytest.mark.asyncio
    async def test_multiple_displays(self, sample_image: Image.Image) -> None:
        """Test display count tracking."""
        display = MockDisplay()
        await display.show_image(sample_image)
        await display.show_image(sample_image)
        await display.show_image(sample_image)

        assert display.display_count == 3

    def test_dimensions(self) -> None:
        """Test display dimensions."""
        display = MockDisplay()
        assert display.width == 1600
        assert display.height == 1200

        custom_display = MockDisplay(width=800, height=480)
        assert custom_display.width == 800
        assert custom_display.height == 480

    @pytest.mark.asyncio
    async def test_wrong_image_size_raises_error(self) -> None:
        """Test that wrong image size raises DisplayError."""
        display = MockDisplay()
        wrong_size_image = Image.new("RGB", (800, 600), "green")

        with pytest.raises(DisplayError) as exc_info:
            await display.show_image(wrong_size_image)

        assert "800x600" in str(exc_info.value)
        assert "1600x1200" in str(exc_info.value)
        assert display.display_count == 0  # No display update happened


class TestDisplayCommand:
    """Tests for DisplayCommand handling."""

    def test_display_command_validation(self, sample_display_command: DisplayCommand) -> None:
        """Test that display command has required fields."""
        assert sample_display_command.action == "display"
        assert sample_display_command.image_path is not None
        assert sample_display_command.image_id is not None

    def test_clear_command_validation(self, sample_clear_command: DisplayCommand) -> None:
        """Test that clear command is valid without image fields."""
        assert sample_clear_command.action == "clear"
        assert sample_clear_command.image_path is None
        assert sample_clear_command.image_id is None


class TestRegistrationResponse:
    """Tests for RegistrationResponse handling."""

    def test_registration_response_fields(self, sample_registration_response: RegistrationResponse) -> None:
        """Test registration response contains MinIO credentials."""
        assert sample_registration_response.minio_endpoint is not None
        assert sample_registration_response.minio_bucket is not None
        assert sample_registration_response.minio_access_key is not None
        assert sample_registration_response.minio_secret_key is not None
