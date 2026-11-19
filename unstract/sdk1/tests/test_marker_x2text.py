from unittest.mock import MagicMock, patch

import pytest
from unstract.sdk1.adapters.exceptions import AdapterError
from unstract.sdk1.adapters.x2text.marker.src import MarkerAdapter

CFG = {"url": "http://marker:8001/", "output_format": "markdown", "page_range": "0-2"}


def _fs(data=b"%PDF"):
    fs = MagicMock()
    fs.read.return_value = data
    fs.mime_type.return_value = "application/pdf"
    return fs


def _resp(payload, ok=True):
    r = MagicMock(ok=ok)
    r.json.return_value = payload
    return r


def test_process_posts_upload_and_writes_output():
    fs = _fs()
    with patch("requests.post", return_value=_resp({"success": True, "output": "# hi"})) as post:
        res = MarkerAdapter(CFG).process("/in.pdf", "/out.txt", fs=fs)
    assert res.extracted_text == "# hi"
    assert post.call_args.args[0] == "http://marker:8001/marker/upload"
    assert post.call_args.kwargs["data"]["page_range"] == "0-2"
    fs.write.assert_called_once()


def test_process_failure_raises():
    with patch("requests.post", return_value=_resp({"success": False, "error": "boom"})):
        with pytest.raises(AdapterError, match="boom"):
            MarkerAdapter(CFG).process("/in.pdf", fs=_fs())


def test_test_connection_ok():
    with patch("requests.get", return_value=_resp({}, ok=True)):
        assert MarkerAdapter(CFG).test_connection() is True
