"""MinIO client for fetching images from object storage."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from minio import Minio
from PIL import Image

from private_assistant_display_controller.exceptions import CommunicationError

logger = logging.getLogger(__name__)


class MinIOImageClient:
    """Client for fetching images from MinIO object storage.

    The MinIO SDK is synchronous, so operations are wrapped with
    run_in_executor to avoid blocking the async event loop.
    """

    def __init__(self, executor: ThreadPoolExecutor | None = None) -> None:
        """Initialize the MinIO client.

        Args:
            executor: Optional thread pool executor for async operations.
        """
        self._client: Minio | None = None
        self._bucket: str | None = None
        self._executor = executor or ThreadPoolExecutor(max_workers=2)

    def configure(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
    ) -> None:
        """Configure the MinIO client with credentials.

        Typically called after receiving RegistrationResponse from MQTT.

        Args:
            endpoint: MinIO server endpoint (host:port).
            access_key: MinIO access key.
            secret_key: MinIO secret key.
            bucket: Bucket name containing images.
            secure: Whether to use HTTPS.
        """
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._bucket = bucket
        logger.info("MinIO client configured for endpoint: %s, bucket: %s", endpoint, bucket)

    @property
    def is_configured(self) -> bool:
        """Check if the client has been configured with credentials."""
        return self._client is not None and self._bucket is not None

    async def fetch_image(self, object_path: str) -> Image.Image:
        """Fetch an image from MinIO and return as PIL Image.

        Runs the synchronous MinIO SDK operation in a thread pool
        to avoid blocking the async event loop.

        Args:
            object_path: Path to the object in MinIO bucket.

        Returns:
            PIL Image object.

        Raises:
            CommunicationError: If MinIO is not configured or fetch fails.
        """
        if not self.is_configured:
            raise CommunicationError("MinIO client not configured. Await registration.")

        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(
                self._executor,
                self._fetch_image_sync,
                object_path,
            )
        except Exception as e:
            logger.exception("Failed to fetch image from MinIO: %s", object_path)
            raise CommunicationError(f"Failed to fetch image: {e}") from e

    def _fetch_image_sync(self, object_path: str) -> Image.Image:
        """Synchronous image fetch implementation.

        Args:
            object_path: Path to the object in MinIO bucket.

        Returns:
            PIL Image object.
        """
        assert self._client is not None
        assert self._bucket is not None

        logger.debug("Fetching image from MinIO: %s/%s", self._bucket, object_path)
        response = self._client.get_object(self._bucket, object_path)
        try:
            image_data = BytesIO(response.read())
            image = Image.open(image_data)
            # Load the image data into memory so we can close the response
            image.load()
            logger.debug("Successfully fetched image: %s (size: %s)", object_path, image.size)
            return image
        finally:
            response.close()
            response.release_conn()

    def close(self) -> None:
        """Clean up resources."""
        if self._executor:
            self._executor.shutdown(wait=False)
