"""Main controller orchestrating all display controller components."""

import asyncio
import logging

from private_assistant_display_controller.config import Settings
from private_assistant_display_controller.display import DisplayInterface, create_display
from private_assistant_display_controller.exceptions import CommunicationError, DisplayError
from private_assistant_display_controller.minio_client import MinIOImageClient
from private_assistant_display_controller.models import (
    DeviceAcknowledge,
    DeviceRegistration,
    DisplayCommand,
    DisplayInfo,
    RegistrationResponse,
)
from private_assistant_display_controller.mqtt_client import MQTTClient

logger = logging.getLogger(__name__)


class DisplayController:
    """Main controller orchestrating display operations.

    Coordinates MQTT communication, MinIO image fetching, and display updates.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize the display controller.

        Args:
            settings: Application settings.
        """
        self._settings = settings
        self._current_image_id: str | None = None
        self._is_registered = asyncio.Event()
        self._shutdown_event = asyncio.Event()

        # Initialize components
        self._minio = MinIOImageClient()
        self._display: DisplayInterface = create_display(
            mock=settings.display.mock,
            orientation=settings.display.orientation,
            mock_width=settings.display.mock_width,
            mock_height=settings.display.mock_height,
        )
        self._mqtt = MQTTClient(
            config=settings.mqtt,
            device_id=settings.device.id,
            on_command=self._handle_command,
            on_registration_response=self._handle_registration_response,
        )

    async def run(self) -> None:
        """Main entry point - start all async tasks.

        Runs until shutdown is requested via shutdown() method.
        """
        logger.info("Starting display controller for device: %s", self._settings.device.id)

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self._mqtt.run(), name="mqtt")
                tg.create_task(self._registration_loop(), name="registration")
                tg.create_task(self._shutdown_monitor(), name="shutdown_monitor")
        except* Exception as eg:
            for exc in eg.exceptions:
                if not isinstance(exc, asyncio.CancelledError):
                    logger.exception("Task failed: %s", exc)
        finally:
            await self._cleanup()

    async def shutdown(self) -> None:
        """Request graceful shutdown."""
        logger.info("Shutdown requested")
        self._shutdown_event.set()

    async def _shutdown_monitor(self) -> None:
        """Monitor for shutdown signal and cancel tasks."""
        await self._shutdown_event.wait()
        raise asyncio.CancelledError("Shutdown requested")

    async def _cleanup(self) -> None:
        """Clean up resources on shutdown."""
        logger.info("Cleaning up resources...")
        self._minio.close()
        if hasattr(self._display, "close"):
            self._display.close()  # type: ignore[attr-defined]
        await self._mqtt.disconnect()

    async def _registration_loop(self) -> None:
        """Send registration on startup and retry until acknowledged.

        Runs until registration is successful, then exits.
        """
        registration = DeviceRegistration(
            device_id=self._settings.device.id,
            display=DisplayInfo(
                width=self._display.width,
                height=self._display.height,
                orientation=self._settings.display.orientation,
            ),
            room=self._settings.device.room,
        )

        retry_interval = 10
        max_retry_interval = 60

        while not self._is_registered.is_set():
            try:
                logger.info("Sending registration...")
                await self._mqtt.publish_registration(registration)

                # Wait for response with timeout
                try:
                    await asyncio.wait_for(
                        self._is_registered.wait(),
                        timeout=30.0,
                    )
                    logger.info("Registration successful")
                    return
                except TimeoutError:
                    logger.warning(
                        "Registration response timeout. Retrying in %d seconds...",
                        retry_interval,
                    )
            except Exception:
                logger.exception("Registration failed")

            await asyncio.sleep(retry_interval)
            retry_interval = min(retry_interval * 2, max_retry_interval)

    async def _handle_registration_response(self, response: RegistrationResponse) -> None:
        """Process registration response and configure MinIO client.

        Args:
            response: Registration response containing MinIO credentials.
        """
        logger.info(
            "Received registration response: status=%s, endpoint=%s",
            response.status,
            response.minio_endpoint,
        )

        self._minio.configure(
            endpoint=response.minio_endpoint,
            access_key=response.minio_access_key,
            secret_key=response.minio_secret_key,
            bucket=response.minio_bucket,
            secure=response.minio_secure,
        )
        self._is_registered.set()

    async def _handle_command(self, command: DisplayCommand) -> None:
        """Process incoming display commands.

        Args:
            command: Command to process.
        """
        logger.info("Received command: action=%s, image_id=%s", command.action, command.image_id)

        try:
            match command.action:
                case "display":
                    await self._handle_display(command)
                case "clear":
                    await self._handle_clear()
                case "status":
                    await self._send_acknowledge(success=True)
                case _:
                    logger.warning("Unknown command action: %s", command.action)
                    await self._send_acknowledge(
                        image_id=command.image_id,
                        success=False,
                        error=f"Unknown action: {command.action}",
                    )
        except (CommunicationError, DisplayError) as e:
            logger.exception("Command failed: %s", command.action)
            await self._send_acknowledge(
                image_id=command.image_id,
                success=False,
                error=str(e),
            )
        except Exception as e:
            logger.exception("Unexpected error handling command")
            await self._send_acknowledge(
                image_id=command.image_id,
                success=False,
                error=f"Unexpected error: {e}",
            )

    async def _handle_display(self, command: DisplayCommand) -> None:
        """Fetch and display an image.

        Args:
            command: Display command with image path.

        Raises:
            ValueError: If image_path or image_id is missing.
            CommunicationError: If MinIO fetch fails.
            DisplayError: If display update fails.
        """
        if not command.image_path or not command.image_id:
            raise ValueError("display command requires image_path and image_id")

        if not self._minio.is_configured:
            raise CommunicationError("MinIO not configured - awaiting registration")

        logger.info("Fetching image: %s", command.image_path)
        image = await self._minio.fetch_image(command.image_path)

        logger.info("Displaying image: %s", command.image_id)
        await self._display.show_image(
            image,
            saturation=self._settings.display.saturation,
        )

        self._current_image_id = command.image_id

        await self._send_acknowledge(
            image_id=command.image_id,
            success=True,
        )

    async def _handle_clear(self) -> None:
        """Clear the display.

        Raises:
            DisplayError: If display clear fails.
        """
        logger.info("Clearing display")
        await self._display.clear()
        self._current_image_id = None

        await self._send_acknowledge(success=True)

    async def _send_acknowledge(
        self,
        image_id: str | None = None,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        """Send acknowledgment after command processing.

        Args:
            image_id: Image ID if applicable.
            success: Whether the command was successful.
            error: Error message if command failed.
        """
        acknowledge = DeviceAcknowledge(
            device_id=self._settings.device.id,
            image_id=image_id or self._current_image_id,
            successful_display_change=success,
            error=error,
        )

        try:
            await self._mqtt.publish_acknowledge(acknowledge)
        except Exception:
            logger.exception("Failed to send acknowledgment")
