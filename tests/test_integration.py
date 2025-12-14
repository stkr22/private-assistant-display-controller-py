"""End-to-end integration tests for the Display Controller.

These tests validate the complete controller workflow with real external services:
- MQTT broker (Mosquitto)
- Controller running in background with MockDisplay

Test flow:
1. Start controller in background with mock display
2. Send registration response via MQTT
3. Send display commands via MQTT
4. Assert controller publishes correct acknowledgments

Run these tests with:
    pytest tests/test_integration.py -v -m integration

Requirements:
- Compose services (Mosquitto) must be running
"""

import asyncio
import contextlib
import json
import logging
import os
import uuid
from unittest.mock import AsyncMock, patch

import aiomqtt
import pytest
from PIL import Image

from private_assistant_display_controller.config import (
    DeviceConfig,
    DisplayConfig,
    MinIOConfig,
    MQTTConfig,
    Settings,
)
from private_assistant_display_controller.controller import DisplayController
from private_assistant_display_controller.models import (
    DisplayCommand,
    RegistrationResponse,
)

# Mark all tests in this module as integration tests
# These tests require external services and are skipped by default
pytestmark = [pytest.mark.integration]

# Logger for test debugging
logger = logging.getLogger(__name__)


@pytest.fixture
def mqtt_config():
    """Get MQTT configuration from environment variables."""
    return {
        "host": os.getenv("MQTT_HOST", "mosquitto"),
        "port": int(os.getenv("MQTT_PORT", "1883")),
    }


@pytest.fixture
def test_device_id():
    """Generate a unique device ID for each test to avoid conflicts."""
    return f"test-device-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def controller_settings(mqtt_config, test_device_id) -> Settings:
    """Create settings for the controller under test.

    Uses model_construct to bypass pydantic-settings env var reading,
    avoiding conflicts with devcontainer environment variables.
    """
    # Build nested configs without env var interference
    device = DeviceConfig.model_construct(id=test_device_id, room="Integration Test Room")
    mqtt = MQTTConfig.model_construct(
        host=mqtt_config["host"],
        port=mqtt_config["port"],
        username=None,
        password=None,
        client_id=None,
        transport="tcp",
        websocket_path=None,
        tls=False,
    )
    minio = MinIOConfig.model_construct(
        endpoint="localhost:9000",
        bucket="test-bucket",
        access_key=None,
        secret_key=None,
        secure=False,
    )
    display = DisplayConfig.model_construct(
        orientation="landscape",
        saturation=0.5,
        mock=True,
        mock_width=1600,
        mock_height=1200,
    )

    return Settings.model_construct(
        device=device,
        mqtt=mqtt,
        minio=minio,
        display=display,
        config_file=None,
    )


@pytest.fixture
def sample_registration_response() -> RegistrationResponse:
    """Create a registration response for testing."""
    return RegistrationResponse(
        status="registered",
        minio_endpoint="minio:9000",
        minio_bucket="inky-images",
        minio_access_key="test-access-key",
        minio_secret_key="test-secret-key",
        minio_secure=False,
    )


@pytest.fixture
def mock_minio_image():
    """Create a mock image for MinIO responses."""
    return Image.new("RGB", (1600, 1200), color="blue")


@pytest.fixture
async def mqtt_test_client(mqtt_config):
    """Create an MQTT test client for sending commands and receiving responses."""
    async with aiomqtt.Client(
        hostname=mqtt_config["host"],
        port=mqtt_config["port"],
        identifier=f"test-client-{uuid.uuid4().hex[:8]}",
    ) as client:
        yield client


@pytest.fixture
async def running_controller(
    controller_settings,
    sample_registration_response,
    mock_minio_image,
    mqtt_config,
    test_device_id,
):
    """Start the controller in background with mocked MinIO.

    The controller will:
    1. Connect to MQTT
    2. Send registration to inky/register
    3. Wait for registration response on inky/{device_id}/registered
    4. Listen for commands on inky/{device_id}/command
    5. Send acknowledgments to inky/{device_id}/status
    """
    # Create a mock for MinIO fetch_image
    mock_fetch = AsyncMock(return_value=mock_minio_image)

    # Patch the MinIO client's fetch_image method
    with patch(
        "private_assistant_display_controller.minio_client.MinIOImageClient.fetch_image",
        mock_fetch,
    ):
        controller = DisplayController(controller_settings)

        # Start controller as background task
        controller_task = asyncio.create_task(controller.run())

        # Wait for controller to initialize and connect to MQTT
        await asyncio.sleep(1.0)

        # Send registration response to complete setup
        async with aiomqtt.Client(
            hostname=mqtt_config["host"],
            port=mqtt_config["port"],
            identifier=f"setup-client-{uuid.uuid4().hex[:8]}",
        ) as setup_client:
            registered_topic = f"inky/{test_device_id}/registered"
            await setup_client.publish(
                registered_topic,
                payload=sample_registration_response.model_dump_json(),
                qos=1,
            )
            logger.debug("Sent registration response to %s", registered_topic)

        # Wait for controller to process registration
        await asyncio.sleep(0.5)

        yield {
            "controller": controller,
            "task": controller_task,
            "mock_fetch": mock_fetch,
            "device_id": test_device_id,
        }

        # Cleanup: Shutdown controller
        await controller.shutdown()

        # Give shutdown a moment to propagate
        await asyncio.sleep(0.2)

        # Cancel the task if still running
        if not controller_task.done():
            controller_task.cancel()

        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(controller_task, timeout=2.0)


class TestRegistrationFlow:
    """Tests for device registration workflow."""

    @pytest.mark.asyncio
    async def test_controller_sends_registration(
        self,
        controller_settings,
        mqtt_config,
        test_device_id,
    ):
        """Test that controller sends registration message on startup.

        Flow:
        1. Subscribe to inky/register
        2. Start controller
        3. Assert registration message is received
        """
        registration_received = False
        received_device_id = None

        async with aiomqtt.Client(
            hostname=mqtt_config["host"],
            port=mqtt_config["port"],
            identifier=f"reg-test-{uuid.uuid4().hex[:8]}",
        ) as client:
            # Subscribe to registration topic
            await client.subscribe("inky/register", qos=1)

            # Start controller in background
            controller = DisplayController(controller_settings)
            controller_task = asyncio.create_task(controller.run())

            try:
                async with asyncio.timeout(5):
                    async for message in client.messages:
                        topic = str(message.topic)
                        if topic == "inky/register":
                            payload = message.payload
                            if isinstance(payload, (bytes, bytearray)):
                                data = json.loads(payload.decode("utf-8"))
                            else:
                                data = json.loads(str(payload))
                            received_device_id = data.get("device_id")
                            # Filter by our device ID to avoid interference from parallel tests
                            if received_device_id == test_device_id:
                                registration_received = True
                                break
            except TimeoutError:
                pass
            finally:
                await controller.shutdown()
                await asyncio.sleep(0.2)
                if not controller_task.done():
                    controller_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                    await asyncio.wait_for(controller_task, timeout=2.0)

        assert registration_received, "Did not receive registration within timeout"
        assert received_device_id == test_device_id


class TestDisplayCommands:
    """Tests for display command handling."""

    @pytest.mark.asyncio
    async def test_display_command_success(
        self,
        running_controller,
        mqtt_test_client,
    ):
        """Test successful display command flow.

        Flow:
        1. Controller is running and registered
        2. Send display command via MQTT
        3. Assert successful acknowledgment is received
        """
        device_id = running_controller["device_id"]
        command_topic = f"inky/{device_id}/command"
        status_topic = f"inky/{device_id}/status"

        # Subscribe to status topic for acknowledgments
        await mqtt_test_client.subscribe(status_topic, qos=1)

        # Send display command
        image_id = str(uuid.uuid4())
        display_command = DisplayCommand(
            action="display",
            image_path="test/images/landscape.jpg",
            image_id=image_id,
            title="Test Landscape",
        )
        await mqtt_test_client.publish(
            command_topic,
            payload=display_command.model_dump_json(),
            qos=1,
        )

        # Wait for acknowledgment
        ack_received = False
        ack_success = False
        received_image_id = None

        try:
            async with asyncio.timeout(5):
                async for message in mqtt_test_client.messages:
                    topic = str(message.topic)
                    if topic == status_topic:
                        payload = message.payload
                        if isinstance(payload, (bytes, bytearray)):
                            data = json.loads(payload.decode("utf-8"))
                        else:
                            data = json.loads(str(payload))
                        ack_received = True
                        ack_success = data.get("successful_display_change", False)
                        received_image_id = data.get("image_id")
                        break
        except TimeoutError:
            pass

        assert ack_received, "Did not receive acknowledgment within timeout"
        assert ack_success, "Display command was not successful"
        assert received_image_id == image_id

    @pytest.mark.asyncio
    async def test_clear_command_success(
        self,
        running_controller,
        mqtt_test_client,
    ):
        """Test successful clear command flow.

        Flow:
        1. Controller is running and registered
        2. Send clear command via MQTT
        3. Assert successful acknowledgment is received
        """
        device_id = running_controller["device_id"]
        command_topic = f"inky/{device_id}/command"
        status_topic = f"inky/{device_id}/status"

        # Subscribe to status topic for acknowledgments
        await mqtt_test_client.subscribe(status_topic, qos=1)

        # Send clear command
        clear_command = DisplayCommand(action="clear")
        await mqtt_test_client.publish(
            command_topic,
            payload=clear_command.model_dump_json(),
            qos=1,
        )

        # Wait for acknowledgment
        ack_received = False
        ack_success = False

        try:
            async with asyncio.timeout(5):
                async for message in mqtt_test_client.messages:
                    topic = str(message.topic)
                    if topic == status_topic:
                        payload = message.payload
                        if isinstance(payload, (bytes, bytearray)):
                            data = json.loads(payload.decode("utf-8"))
                        else:
                            data = json.loads(str(payload))
                        ack_received = True
                        ack_success = data.get("successful_display_change", False)
                        break
        except TimeoutError:
            pass

        assert ack_received, "Did not receive acknowledgment within timeout"
        assert ack_success, "Clear command was not successful"

    @pytest.mark.asyncio
    async def test_status_command(
        self,
        running_controller,
        mqtt_test_client,
    ):
        """Test status command returns current state.

        Flow:
        1. Controller is running and registered
        2. Send status command via MQTT
        3. Assert acknowledgment with current state is received
        """
        device_id = running_controller["device_id"]
        command_topic = f"inky/{device_id}/command"
        status_topic = f"inky/{device_id}/status"

        # Subscribe to status topic
        await mqtt_test_client.subscribe(status_topic, qos=1)

        # Send status command
        status_command = DisplayCommand(action="status")
        await mqtt_test_client.publish(
            command_topic,
            payload=status_command.model_dump_json(),
            qos=1,
        )

        # Wait for acknowledgment
        ack_received = False
        ack_success = False

        try:
            async with asyncio.timeout(5):
                async for message in mqtt_test_client.messages:
                    topic = str(message.topic)
                    if topic == status_topic:
                        payload = message.payload
                        if isinstance(payload, (bytes, bytearray)):
                            data = json.loads(payload.decode("utf-8"))
                        else:
                            data = json.loads(str(payload))
                        ack_received = True
                        ack_success = data.get("successful_display_change", False)
                        break
        except TimeoutError:
            pass

        assert ack_received, "Did not receive acknowledgment within timeout"
        assert ack_success, "Status command should report success"


class TestErrorHandling:
    """Tests for error handling scenarios."""

    @pytest.mark.asyncio
    async def test_display_command_without_image_path(
        self,
        running_controller,
        mqtt_test_client,
    ):
        """Test display command without image_path returns error.

        Flow:
        1. Controller is running and registered
        2. Send display command without image_path
        3. Assert error acknowledgment is received
        """
        device_id = running_controller["device_id"]
        command_topic = f"inky/{device_id}/command"
        status_topic = f"inky/{device_id}/status"

        # Subscribe to status topic
        await mqtt_test_client.subscribe(status_topic, qos=1)

        # Send invalid display command (missing image_path)
        invalid_command = DisplayCommand(
            action="display",
            image_id="some-id",
            # image_path is missing
        )
        await mqtt_test_client.publish(
            command_topic,
            payload=invalid_command.model_dump_json(),
            qos=1,
        )

        # Wait for error acknowledgment
        ack_received = False
        ack_success = None
        error_message = None

        try:
            async with asyncio.timeout(5):
                async for message in mqtt_test_client.messages:
                    topic = str(message.topic)
                    if topic == status_topic:
                        payload = message.payload
                        if isinstance(payload, (bytes, bytearray)):
                            data = json.loads(payload.decode("utf-8"))
                        else:
                            data = json.loads(str(payload))
                        ack_received = True
                        ack_success = data.get("successful_display_change")
                        error_message = data.get("error")
                        break
        except TimeoutError:
            pass

        assert ack_received, "Did not receive acknowledgment within timeout"
        assert ack_success is False, "Command should have failed"
        assert error_message is not None, "Error message should be present"

    @pytest.mark.asyncio
    async def test_minio_fetch_failure(
        self,
        controller_settings,
        sample_registration_response,
        mqtt_config,
        test_device_id,
    ):
        """Test handling of MinIO fetch failures.

        Flow:
        1. Start controller with MinIO fetch mocked to raise exception
        2. Send display command
        3. Assert error acknowledgment is received
        """
        # Patch MinIO to raise an exception
        mock_fetch = AsyncMock(side_effect=Exception("MinIO connection failed"))

        with patch(
            "private_assistant_display_controller.minio_client.MinIOImageClient.fetch_image",
            mock_fetch,
        ):
            controller = DisplayController(controller_settings)
            controller_task = asyncio.create_task(controller.run())

            await asyncio.sleep(1.0)

            # Send registration response
            async with aiomqtt.Client(
                hostname=mqtt_config["host"],
                port=mqtt_config["port"],
                identifier=f"setup-{uuid.uuid4().hex[:8]}",
            ) as setup_client:
                await setup_client.publish(
                    f"inky/{test_device_id}/registered",
                    payload=sample_registration_response.model_dump_json(),
                    qos=1,
                )

            await asyncio.sleep(0.5)

            # Subscribe and send display command
            async with aiomqtt.Client(
                hostname=mqtt_config["host"],
                port=mqtt_config["port"],
                identifier=f"test-{uuid.uuid4().hex[:8]}",
            ) as client:
                status_topic = f"inky/{test_device_id}/status"
                await client.subscribe(status_topic, qos=1)

                display_command = DisplayCommand(
                    action="display",
                    image_path="test/image.jpg",
                    image_id="test-123",
                )
                await client.publish(
                    f"inky/{test_device_id}/command",
                    payload=display_command.model_dump_json(),
                    qos=1,
                )

                ack_received = False
                ack_success = None
                error_message = None

                try:
                    async with asyncio.timeout(5):
                        async for message in client.messages:
                            topic = str(message.topic)
                            if topic == status_topic:
                                payload = message.payload
                                if isinstance(payload, (bytes, bytearray)):
                                    data = json.loads(payload.decode("utf-8"))
                                else:
                                    data = json.loads(str(payload))
                                ack_received = True
                                ack_success = data.get("successful_display_change")
                                error_message = data.get("error")
                                break
                except TimeoutError:
                    pass

            await controller.shutdown()
            await asyncio.sleep(0.2)
            if not controller_task.done():
                controller_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(controller_task, timeout=2.0)

        assert ack_received, "Did not receive acknowledgment within timeout"
        assert ack_success is False, "Command should have failed"
        assert error_message is not None, "Error message should be present"
