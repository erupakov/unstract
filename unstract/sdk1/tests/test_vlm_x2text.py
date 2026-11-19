import io
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from unstract.sdk1.adapters.exceptions import AdapterError
from unstract.sdk1.adapters.x2text.vlm.src import VLMAdapter

CFG = {"api_base": "http://qwen:3001/v1/", "model": "qwen-vl", "max_side": 512}


def _png(size=(1000, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


def _pdf(pages=2) -> bytes:
    buf = io.BytesIO()
    imgs = [Image.new("RGB", (200, 300), "white") for _ in range(pages)]
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def _fs(data):
    fs = MagicMock()
    fs.read.return_value = data
    return fs


def _resp(text):
    r = MagicMock()
    r.json.return_value = {"choices": [{"message": {"content": text}}]}
    return r


def test_image_sent_downscaled_and_text_returned():
    with patch("requests.post", return_value=_resp(" hello ")) as post:
        res = VLMAdapter(CFG).process("/in.png", fs=_fs(_png()))
    assert res.extracted_text == "hello"
    assert post.call_args.args[0] == "http://qwen:3001/v1/chat/completions"
    body = post.call_args.kwargs["json"]
    assert body["model"] == "qwen-vl"
    url = body["messages"][0]["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


def test_pdf_pages_joined():
    with patch("requests.post", side_effect=[_resp("p1"), _resp("p2")]) as post:
        res = VLMAdapter(CFG).process("/in.pdf", fs=_fs(_pdf(2)))
    assert res.extracted_text == "p1\n\np2"
    assert post.call_count == 2


def test_page_cap_rejected():
    with pytest.raises(AdapterError, match="above the limit"):
        VLMAdapter({**CFG, "max_pages": 1}).process("/in.pdf", fs=_fs(_pdf(2)))


def test_garbage_input_rejected():
    with pytest.raises(AdapterError, match="Unsupported"):
        VLMAdapter(CFG).process("/in.bin", fs=_fs(b"not an image"))


def test_test_connection():
    with patch("requests.get", return_value=MagicMock(status_code=200, ok=True)):
        assert VLMAdapter(CFG).test_connection() is True
