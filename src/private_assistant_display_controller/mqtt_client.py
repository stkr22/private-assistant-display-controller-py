"""Async MQTT client for device communication."""

import asyncio
import logging
import ssl
from collections.abc import Awaitable, Callable

import aiomqtt

from private_assistant_display_controller.config import MQTTConfig
from private_assistant_display_controller.models import (
    DeviceAcknowledge,
    DeviceRegistration,
    DisplayCommand,
    RegistrationResponse,
)

logger = logging.getLogger(__name__)

CommandHandler = Callable[[DisplayCommand], Awaitable[None]]
RegistrationHandler = Callable[[RegistrationResponse], Awaitable[None]]


class MQTTClient:
    """Async MQTT client for display controller communication.

    Handles connection management, message routing, and auto-reconnection.
    """

    # MQTT Topic templates
    REGISTER_TOPIC = "inky/register"
    COMMAND_TOPIC_TEMPLATE = "inky/{device_id}/command"
    STATUS_TOPIC_TEMPLATE = "inky/{device_id}/status"
    REGISTERED_TOPIC_TEMPLATE = "inky/{device_id}/registered"

    def __init__(
        self,
        config: MQTTConfig,
        device_id: str,
        on_command: CommandHandler,
        on_registration_response: RegistrationHandler,
    ) -> None:
        """Initialize the MQTT client.

        Args:
            config: MQTT connection configuration.
            device_id: Device identifier for topic subscription.
            on_command: Callback for incoming display commands.
            on_registration_response: Callback for registration responses.
        """
        self._config = config
        self._device_id = device_id
        self._on_command = on_command
        self._on_registration_response = on_registration_response
        self._client: aiomqtt.Client | None = None
        self._connected = asyncio.Event()

    @property
    def command_topic(self) -> str:
        """Topic for receiving display commands."""
        return self.COMMAND_TOPIC_TEMPLATE.format(device_id=self._device_id)

    @property
    def status_topic(self) -> str:
        """Topic for publishing status/acknowledgments."""
        return self.STATUS_TOPIC_TEMPLATE.format(device_id=self._device_id)

    @property
    def registered_topic(self) -> str:
        """Topic for receiving registration responses."""
        return self.REGISTERED_TOPIC_TEMPLATE.format(device_id=self._device_id)

    async def run(self) -> None:
        """Main MQTT loop with automatic reconnection.

        This method runs indefinitely, maintaining the MQTT connection
        and processing incoming messages.
        """
        reconnect_interval = 5
        max_reconnect_interval = 60

        while True:
            try:
                password = self._config.password.get_secret_value() if self._config.password else None
                tls_context = ssl.create_default_context() if self._config.tls else None
                async with aiomqtt.Client(
                    hostname=self._config.host,
                    port=self._config.port,
                    username=self._config.username,
                    password=password,
                    identifier=self._config.client_id or f"inky-{self._device_id}",
                    transport=self._config.transport,
                    websocket_path=self._config.websocket_path,
                    tls_context=tls_context,
                ) as client:
                    self._client = client
                    self._connected.set()
                    logger.info(
                        "Connected to MQTT broker at %s:%d",
                        self._config.host,
                        self._config.port,
                    )

                    # Subscribe to topics
                    await client.subscribe(self.command_topic, qos=1)
                    logger.info("Subscribed to command topic: %s", self.command_topic)

                    await client.subscribe(self.registered_topic, qos=1)
                    logger.info("Subscribed to registered topic: %s", self.registered_topic)

                    # Reset reconnect interval on successful connection
                    reconnect_interval = 5

                    # Process incoming messages
                    async for message in client.messages:
                        await self._handle_message(message)

            except aiomqtt.MqttError as e:
                self._connected.clear()
                self._client = None
                logger.warning(
                    "MQTT connection error: %s. Reconnecting in %d seconds...",
                    e,
                    reconnect_interval,
                )
                await asyncio.sleep(reconnect_interval)
                # Exponential backoff with cap
                reconnect_interval = min(reconnect_interval * 2, max_reconnect_interval)

    async def _handle_message(self, message: aiomqtt.Message) -> None:
        """Route incoming messages to appropriate handlers.

        Args:
            message: MQTT message to process.
        """
        topic = str(message.topic)
        raw_payload = message.payload
        if isinstance(raw_payload, (bytes, bytearray)):
            payload = raw_payload.decode("utf-8")
        elif isinstance(raw_payload, str):
            payload = raw_payload
        else:
            payload = "{}"

        logger.debug("Received message on topic %s: %s", topic, payload[:200])

        try:
            if topic == self.command_topic:
                command = DisplayCommand.model_validate_json(payload)
                await self._on_command(command)
            elif topic == self.registered_topic:
                response = RegistrationResponse.model_validate_json(payload)
                await self._on_registration_response(response)
            else:
                logger.warning("Received message on unexpected topic: %s", topic)
        except Exception:
            logger.exception("Error handling message on topic %s", topic)

    async def publish_registration(self, registration: DeviceRegistration) -> None:
        """Send device registration message.

        Args:
            registration: Registration payload.
        """
        await self._wait_connected()
        assert self._client is not None

        await self._client.publish(
            self.REGISTER_TOPIC,
            payload=registration.model_dump_json(),
            qos=1,
        )
        logger.info("Published registration to %s", self.REGISTER_TOPIC)

    async def publish_acknowledge(self, acknowledge: DeviceAcknowledge) -> None:
        """Send acknowledgment/status message.

        Args:
            acknowledge: Acknowledgment payload.
        """
        await self._wait_connected()
        assert self._client is not None

        await self._client.publish(
            self.status_topic,
            payload=acknowledge.model_dump_json(),
            qos=0,
        )
        logger.debug(
            "Published acknowledgment: success=%s, image_id=%s",
            acknowledge.successful_display_change,
            acknowledge.image_id,
        )

    async def _wait_connected(self, timeout: float = 30.0) -> None:
        """Wait for MQTT connection to be established.

        Args:
            timeout: Maximum time to wait for connection.

        Raises:
            RuntimeError: If connection is not established within timeout.
        """
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except TimeoutError:
            raise RuntimeError("MQTT connection timeout") from None

    async def disconnect(self) -> None:
        """Signal that we want to disconnect."""
        self._connected.clear()
        self._client = None
