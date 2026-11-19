import logging
import os
from io import BytesIO
from typing import Any

import requests
from requests.exceptions import ConnectionError, HTTPError, Timeout
from unstract.sdk1.adapters.exceptions import AdapterError
from unstract.sdk1.adapters.x2text.dto import TextExtractionResult
from unstract.sdk1.adapters.x2text.x2text_adapter import X2TextAdapter
from unstract.sdk1.file_storage import FileStorage, FileStorageProvider

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 600
CONNECT_TIMEOUT = 10
UPLOAD_PATH = "/marker/upload"
# Paths probed by test_connection; FastAPI serves the first two by default.
PROBE_PATHS = ("/openapi.json", "/docs")


class MarkerAdapter(X2TextAdapter):
    def __init__(self, settings: dict[str, Any]) -> None:
        """Initialize the Marker text extraction adapter.

        Args:
            settings: Configuration dictionary with the Marker server URL and
                processing options.
        """
        super().__init__("Marker")
        self.config = settings

    SCHEMA_PATH = f"{os.path.dirname(__file__)}/static/json_schema.json"

    @staticmethod
    def get_id() -> str:
        return "marker|081b6d41-fe00-4584-b468-13f28b08475d"

    @staticmethod
    def get_name() -> str:
        return "Marker"

    @staticmethod
    def get_description() -> str:
        return "Marker X2Text (self-hosted marker_server)"

    @staticmethod
    def get_icon() -> str:
        return "/icons/adapter-icons/noOpx2Text.png"

    def _base_url(self) -> str:
        url = (self.config.get("url") or "").strip().rstrip("/")
        if not url:
            raise AdapterError("Marker: server URL is not configured")
        return url

    def _timeout(self) -> float:
        try:
            return float(self.config.get("timeout") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            return float(DEFAULT_TIMEOUT)

    def process(
        self,
        input_file_path: str,
        output_file_path: str | None = None,
        fs: FileStorage | None = None,
        **kwargs: dict[Any, Any],
    ) -> TextExtractionResult:
        if fs is None:
            fs = FileStorage(provider=FileStorageProvider.LOCAL)
        try:
            file_bytes = fs.read(path=input_file_path, mode="rb")
            mime_type = fs.mime_type(path=input_file_path)
        except OSError as e:
            logger.error(f"OS error while reading {input_file_path}: {e}")
            raise AdapterError(str(e)) from e

        data = {
            "output_format": self.config.get("output_format") or "markdown",
            "force_ocr": str(bool(self.config.get("force_ocr", False))).lower(),
            "paginate_output": str(bool(self.config.get("paginate_output", False))).lower(),
        }
        page_range = (self.config.get("page_range") or "").strip()
        if page_range:
            data["page_range"] = page_range
        files = {
            "file": (
                os.path.basename(input_file_path),
                BytesIO(file_bytes),
                mime_type or "application/octet-stream",
            )
        }
        try:
            response = requests.post(
                f"{self._base_url()}{UPLOAD_PATH}",
                data=data,
                files=files,
                timeout=(CONNECT_TIMEOUT, self._timeout()),
            )
            response.raise_for_status()
        except ConnectionError as e:
            logger.error(f"Marker connection error: {e}")
            raise AdapterError(
                "Unable to connect to the Marker server, please check the URL"
            ) from e
        except Timeout as e:
            logger.error(f"Marker request timed out: {e}")
            raise AdapterError("Request to the Marker server timed out") from e
        except HTTPError as e:
            logger.error(f"Marker HTTP error: {e}")
            raise AdapterError(f"Marker: {e.response.status_code} {e.response.text[:300]}") from e

        try:
            result = response.json()
        except ValueError as e:
            raise AdapterError("Marker returned a non-JSON response") from e
        if not result.get("success", True):
            raise AdapterError(f"Marker: {result.get('error') or 'extraction failed'}")
        extracted_text = result.get("output")
        if not isinstance(extracted_text, str):
            raise AdapterError("Marker response has no 'output' text")

        if output_file_path:
            fs.write(
                path=output_file_path, mode="w", encoding="utf-8", data=extracted_text
            )
        return TextExtractionResult(extracted_text=extracted_text)

    def test_connection(self) -> bool:
        base = self._base_url()
        last_err: Exception | None = None
        for path in PROBE_PATHS:
            try:
                response = requests.get(f"{base}{path}", timeout=(CONNECT_TIMEOUT, 30))
            except (ConnectionError, Timeout) as e:
                last_err = e
                break
            if response.ok:
                return True
        if last_err is not None:
            raise AdapterError(
                "Unable to connect to the Marker server, please check the URL"
            ) from last_err
        raise AdapterError("Marker server did not respond as expected, check the URL")
