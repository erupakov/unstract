import base64
import io
import logging
import os
from typing import Any

import requests
from requests.exceptions import ConnectionError, HTTPError, Timeout
from unstract.sdk1.adapters.exceptions import AdapterError
from unstract.sdk1.adapters.x2text.dto import TextExtractionResult
from unstract.sdk1.adapters.x2text.x2text_adapter import X2TextAdapter
from unstract.sdk1.file_storage import FileStorage, FileStorageProvider

logger = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "Transcribe all text from this document image exactly as written, keeping its "
    "language. Preserve the reading order and layout. Render tables as Markdown "
    "tables. Include stamps, handwritten notes, numbers, dates and amounts. Do not "
    "translate, summarize or add comments. Output only the transcription."
)
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TIMEOUT = 300
DEFAULT_MAX_SIDE = 2048
DEFAULT_PDF_DPI = 150
DEFAULT_MAX_PAGES = 30
CONNECT_TIMEOUT = 10
PAGE_SEPARATOR = "\n\n"


def _int(value: Any, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


class VLMAdapter(X2TextAdapter):
    def __init__(self, settings: dict[str, Any]) -> None:
        """Initialize the vision-LLM text extraction adapter.

        Args:
            settings: Endpoint, model and image/PDF handling options.
        """
        super().__init__("VLM")
        self.config = settings

    SCHEMA_PATH = f"{os.path.dirname(__file__)}/static/json_schema.json"

    @staticmethod
    def get_id() -> str:
        return "vlm|6f0f5d0e-3b1c-4c0e-9a57-2d7f4f6a8b21"

    @staticmethod
    def get_name() -> str:
        return "Vision LLM"

    @staticmethod
    def get_description() -> str:
        return "Text extraction with a vision LLM (OpenAI-compatible)"

    @staticmethod
    def get_icon() -> str:
        return "/icons/adapter-icons/OpenAICompatible.png"

    # ----------------------------------------------------------------- helpers
    def _base(self) -> str:
        base = (self.config.get("api_base") or "").strip().rstrip("/")
        if not base:
            raise AdapterError("Vision LLM: API Base is not configured")
        return base

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.config.get("api_key")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _prepare_image(self, image: Any) -> str:
        """Orient, downscale and encode a PIL image as a PNG/JPEG data URL."""
        from PIL import ImageOps

        image = ImageOps.exif_transpose(image)
        max_side = _int(self.config.get("max_side"), DEFAULT_MAX_SIDE)
        if max(image.size) > max_side:
            image.thumbnail((max_side, max_side))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"

    def _images_from_file(self, data: bytes) -> list[str]:
        from PIL import Image

        max_pages = _int(self.config.get("max_pages"), DEFAULT_MAX_PAGES)
        if data[:5] == b"%PDF-":
            try:
                import pypdfium2 as pdfium
            except ImportError as e:
                raise AdapterError(
                    "PDF input needs the 'pypdfium2' package in the environment"
                ) from e
            dpi = _int(self.config.get("pdf_dpi"), DEFAULT_PDF_DPI)
            try:
                pdf = pdfium.PdfDocument(data)
                n_pages = len(pdf)
                if n_pages > max_pages:
                    raise AdapterError(
                        f"Document has {n_pages} pages, above the limit of {max_pages}"
                    )
                return [
                    self._prepare_image(pdf[i].render(scale=dpi / 72).to_pil())
                    for i in range(n_pages)
                ]
            except AdapterError:
                raise
            except Exception as e:
                raise AdapterError(f"Unable to render the PDF: {e}") from e
        try:
            with Image.open(io.BytesIO(data)) as img:
                n_frames = getattr(img, "n_frames", 1)
                if n_frames > max_pages:
                    raise AdapterError(
                        f"Image has {n_frames} frames, above the limit of {max_pages}"
                    )
                pages = []
                for i in range(n_frames):
                    img.seek(i)
                    pages.append(self._prepare_image(img.copy()))
                return pages
        except AdapterError:
            raise
        except Exception as e:
            raise AdapterError(f"Unsupported or unreadable image file: {e}") from e

    def _transcribe(self, data_url: str) -> str:
        payload = {
            "model": self.config.get("model"),
            "temperature": 0,
            "max_tokens": _int(self.config.get("max_tokens"), DEFAULT_MAX_TOKENS),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": self.config.get("prompt") or DEFAULT_PROMPT,
                        },
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        }
        timeout = float(self.config.get("timeout") or DEFAULT_TIMEOUT)
        try:
            response = requests.post(
                f"{self._base()}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, timeout),
            )
            response.raise_for_status()
        except ConnectionError as e:
            logger.error(f"VLM connection error: {e}")
            raise AdapterError(
                "Unable to connect to the vision model, please check the API Base"
            ) from e
        except Timeout as e:
            raise AdapterError("Request to the vision model timed out") from e
        except HTTPError as e:
            raise AdapterError(
                f"Vision model: {e.response.status_code} {e.response.text[:300]}"
            ) from e
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise AdapterError("Unexpected response from the vision model") from e
        return (content or "").strip()

    # --------------------------------------------------------------- interface
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
            data = fs.read(path=input_file_path, mode="rb")
        except OSError as e:
            logger.error(f"OS error while reading {input_file_path}: {e}")
            raise AdapterError(str(e)) from e

        pages = self._images_from_file(data)
        texts = [self._transcribe(page) for page in pages]
        extracted_text = PAGE_SEPARATOR.join(t for t in texts if t)

        if output_file_path:
            fs.write(
                path=output_file_path, mode="w", encoding="utf-8", data=extracted_text
            )
        return TextExtractionResult(extracted_text=extracted_text)

    def test_connection(self) -> bool:
        try:
            response = requests.get(
                f"{self._base()}/models",
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, 30),
            )
        except (ConnectionError, Timeout) as e:
            raise AdapterError(
                "Unable to connect to the vision model, please check the API Base"
            ) from e
        if response.status_code in (401, 403):
            raise AdapterError("Vision model rejected the API key")
        if not response.ok:
            raise AdapterError(f"Vision model returned HTTP {response.status_code}")
        return True
