import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest

from perfzy.integrations.record_upload import UploadError, upload_record, validate_upload_settings
from perfzy.storage.sessions import save_session


@contextmanager
def receiver(status=201, body=None, location=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, dict(self.headers.items()), raw))
            data = json.dumps(body if body is not None else {
                "record_id": "saved-123",
                "report_url": "https://example.com/report/saved-123#charts",
            }).encode()
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/records", requests
        finally:
            server.shutdown()
            thread.join()


@pytest.fixture
def recording(tmp_path):
    path = save_session(
        tmp_path, {"id": "android:PRIVATE_SERIAL", "platform": "安卓设备", "name": "测试设备"},
        {"id": "android:PRIVATE_SERIAL:com.example.game", "name": "测试应用",
         "icon": "C:/local/icon.png"},
        {"fps": [{"x": 1, "y": 59.5}]}, [], {"fps", "memory"},
        session_id="sample", session_name="测试记录", duration=5,
    )
    (path.parent / "scenes.json").write_text(json.dumps({"scenes": [
        {"name": "加载", "start": 0, "end": 2, "color": "#123456"},
    ]}), encoding="utf-8")
    return tmp_path, path


def test_upload_posts_latest_saved_data_and_reuses_idempotency_key(recording):
    root, _ = recording
    with receiver() as (url, requests):
        first = upload_record(root, "sample", url, "demo-token")
        second = upload_record(root, "sample", url, "demo-token")
    assert first == second
    assert first["record_id"] == "saved-123"
    assert first["report_url"].endswith("#charts")
    path, headers, raw = requests[0]
    assert path == "/records"
    assert headers["Authorization"] == "Bearer demo-token"
    assert headers["Idempotency-Key"] == requests[1][1]["Idempotency-Key"]
    assert b"PRIVATE_SERIAL" not in raw and b"C:/local/" not in raw and b"demo-token" not in raw
    payload = json.loads(raw)
    assert payload["schema"] == "perfzy.record.v1"
    assert payload["record"]["application"]["package"] == "com.example.game"
    assert payload["record"]["scenes"][0]["name"] == "加载"
    assert "memory" in payload["record"]["enabled_metrics"]
    assert payload["samples"] == {"fps": [{"x": 1, "y": 59.5}]}
    assert payload["screenshots"] == []


def test_screenshots_are_opt_in_and_remain_within_the_record(recording):
    root, path = recording
    data = json.loads(path.read_text(encoding="utf-8"))
    shots = path.parent / "screenshots"
    shots.mkdir()
    image = shots / "1.png"
    content = b"\x89PNG\r\n\x1a\nfake-test-image"
    image.write_bytes(content)
    data["screenshots"] = [{"image": image.as_uri(), "seconds": 1}]
    path.write_text(json.dumps(data), encoding="utf-8")
    with receiver() as (url, requests):
        upload_record(root, "sample", url)
        upload_record(root, "sample", url, include_screenshots=True)
        assert json.loads(requests[0][2])["screenshots"] == []
        screenshot = json.loads(requests[1][2])["screenshots"][0]
        assert screenshot["content_type"] == "image/png"
        assert base64.b64decode(screenshot["data"]) == content
        assert requests[0][1]["Idempotency-Key"] != requests[1][1]["Idempotency-Key"]
        outside = root / "outside.png"
        image.replace(outside)
        data["screenshots"][0]["image"] = outside.as_uri()
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(UploadError, match="截图"):
            upload_record(root, "sample", url, include_screenshots=True)
        assert len(requests) == 2


@pytest.mark.parametrize("url,token", [
    ("file:///c:/test", ""),
    ("https://user:password@example.com", ""),
    ("http://example.com/upload", "token"),
    ("https://example.com/upload", "token\nHeader: x"),
])
def test_reject_invalid_destination_or_credential(url, token):
    with pytest.raises(UploadError):
        validate_upload_settings(url, token)


def test_redirect_does_not_send_record_or_token_to_second_service(recording):
    root, _ = recording
    with receiver() as (other, other_requests), receiver(status=307, location=other) as (url, requests):
        with pytest.raises(UploadError, match="跳转"):
            upload_record(root, "sample", url, "demo-token")
    assert len(requests) == 1 and other_requests == []


def test_accepted_without_report_link_and_malformed_receipt(recording):
    root, _ = recording
    with receiver(status=202, body={"record_id": "pending"}) as (url, _):
        assert upload_record(root, "sample", url) == {"record_id": "pending", "report_url": ""}
    with receiver(body={"record_id": "accepted", "report_url": "file:///c:/local"}) as (url, _):
        with pytest.raises(UploadError, match="回执"):
            upload_record(root, "sample", url)
