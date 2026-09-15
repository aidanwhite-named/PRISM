"""Wire regression: agy 1.2.2 rejects a valid answer after two empty parts."""
import asyncio
import copy
import http.client
import json
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from app.providers import agy_search_compat as compat


def response():
    return {"response": {"candidates": [{
        "content": {"role": "model", "parts": [
            {"thought": True, "text": ""}, {"thought": True, "text": ""},
            {"thoughtSignature": "opaque-signature", "text": "실제 검색 요약"},
        ]},
        "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://example.org"}}]},
        "finishReason": "STOP",
    }], "usageMetadata": {"totalTokenCount": 953}}, "metadata": {}}


def test_only_empty_prefix_is_removed_and_evidence_is_preserved():
    original = response()
    expected = copy.deepcopy(original)
    expected["response"]["candidates"][0]["content"]["parts"] = original["response"]["candidates"][0]["content"]["parts"][2:]
    actual, count = compat.normalize_search_response(json.dumps(original).encode())
    assert count == 2
    assert json.loads(actual) == expected
    assert compat.normalize_search_response(actual) == (actual, 0)


@pytest.mark.parametrize("parts", [
    [{"thought": True, "text": ""}],
    [{"thought": True, "text": ""}, {"thought": True, "text": "private thought"}],
    [{"thought": True, "text": "", "thoughtSignature": "keep"}, {"text": "answer"}],
    [{"thought": True, "text": "", "functionCall": {}}, {"text": "answer"}],
    [{"thought": True, "text": ""}, {"text": " "}],
    [{"text": "answer"}, {"thought": True, "text": ""}],
    [None, {"text": "answer"}],
])
def test_no_answer_is_fabricated_or_meaningful_part_removed(parts):
    payload = response()
    payload["response"]["candidates"][0]["content"]["parts"] = parts
    body = json.dumps(payload).encode()
    assert compat.normalize_search_response(body) == (body, 0)


@pytest.mark.parametrize("body", [b"bad json", b"null", b"[]", b'{}',
                                      b'{"response":{"candidates":null}}',
                                      b'{"error":{"code":429}}'])
def test_malformed_and_error_responses_remain_unchanged(body):
    assert compat.normalize_search_response(body) == (body, 0)


@pytest.fixture
def upstream(monkeypatch):
    state = {"body": json.dumps(response()).encode(), "status": 200,
             "headers": {"Content-Type": "application/json"}, "requests": [],
             "release": threading.Event()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state["requests"].append((self.path, body, dict(self.headers)))
            self.send_response(state["status"])
            for key, value in state["headers"].items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(state["body"])
            self.wfile.flush()
            if state.get("hold_open"):
                state["release"].wait(5)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(compat._Relay, "connection", lambda self:
                        http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3))
    yield state
    state["release"].set()
    server.shutdown()
    server.server_close()
    thread.join(1)


def post(relay, *, path="/v1internal:generateContent", search=True, chunked=False,
         prefix=True, origin=False):
    parsed = urlsplit(relay.url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
    body = json.dumps({"request": {"tools": [{"googleSearch": {}}] if search else []}}).encode()
    headers = {"Authorization": "Bearer test-only", "Content-Type": "application/json"}
    if origin:
        headers["Origin"] = "https://untrusted.example"
    connection.request("POST", (parsed.path if prefix else "") + path,
                       iter([body[:10], body[10:]]) if chunked else body,
                       headers, encode_chunked=chunked)
    result = connection.getresponse()
    return connection, result


def test_chunked_request_and_corrected_response(upstream):
    async def run():
        async with compat.search_compatibility(enabled=True, timeout=3) as relay:
            assert relay.tls.verify_mode == ssl.CERT_REQUIRED and relay.tls.check_hostname
            connection, result = post(relay, chunked=True)
            body = result.read()
            assert result.status == 200
            assert int(result.getheader("Content-Length")) == len(body)
            assert len(json.loads(body)["response"]["candidates"][0]["content"]["parts"]) == 1
            assert relay.corrected_responses == 1 and relay.removed_parts == 2
            path, request, headers = upstream["requests"][0]
            assert path == "/v1internal:generateContent"
            assert json.loads(request)["request"]["tools"] == [{"googleSearch": {}}]
            assert headers["Authorization"] == "Bearer test-only"
            assert headers["Accept-Encoding"] == "identity"
            assert "Transfer-Encoding" not in headers
            connection.close()
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", relay.server_port), timeout=0.2)
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["quota", "redirect", "nonsearch", "compressed"])
def test_other_responses_are_passed_through_without_retry(upstream, mode):
    if mode == "quota":
        upstream.update(status=429, body=b'{"error":{"status":"RESOURCE_EXHAUSTED"}}')
        upstream["headers"]["Retry-After"] = "60"
    elif mode == "redirect":
        upstream.update(status=302, body=b"")
        upstream["headers"]["Location"] = "https://untrusted.example/"
    elif mode == "compressed":
        import gzip
        upstream["body"] = gzip.compress(upstream["body"])
        upstream["headers"]["Content-Encoding"] = "gzip"

    async def run():
        async with compat.search_compatibility(enabled=True, timeout=3) as relay:
            connection, result = post(relay, search=mode != "nonsearch")
            assert result.status == upstream["status"]
            assert result.read() == upstream["body"]
            assert relay.corrected_responses == 0
            assert len(upstream["requests"]) == 1
            for key, value in upstream["headers"].items():
                assert result.getheader(key) == value
            connection.close()
    asyncio.run(run())


@pytest.mark.parametrize("options", [{"prefix": False}, {"origin": True},
                                    {"path": "/v1internal:unknown"},
                                    {"path": "https://untrusted.example/"}])
def test_relay_cannot_be_used_as_an_arbitrary_proxy(upstream, options):
    async def run():
        async with compat.search_compatibility(enabled=True, timeout=3) as relay:
            connection, result = post(relay, **options)
            assert result.status == 403
            result.read()
            connection.close()
            assert not upstream["requests"]
    asyncio.run(run())


def test_sse_is_forwarded_before_upstream_closes_and_cancel_closes_listener(upstream):
    upstream.update(body=b'data: {"text":"first"}\n\n', hold_open=True)
    upstream["headers"]["Content-Type"] = "text/event-stream"

    async def run():
        with pytest.raises(asyncio.CancelledError):
            async with compat.search_compatibility(enabled=True, timeout=3) as relay:
                connection, result = post(relay, path="/v1internal:streamGenerateContent?alt=sse")
                assert result.read(len(upstream["body"])) == upstream["body"]
                assert not upstream["release"].is_set()
                connection.close()
                raise asyncio.CancelledError
        assert relay.stopping
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", relay.server_port), timeout=0.2)
    asyncio.run(run())


@pytest.mark.parametrize("version,search,enabled", [
    ("1.2.2", True, True), ("1.2.2", False, False), ("1.2.3", True, False),
])
async def test_provider_scopes_relay_to_affected_search_and_cleans_up_on_failure(
    monkeypatch, tmp_path, version, search, enabled,
):
    from app.execution import process
    from app.providers.agy_cli import AgyCliProvider
    from app.providers.base import AGY_WEB_SEARCH, ExecutionRequest

    executable = tmp_path / "agy.exe"
    executable.write_bytes(b"test stub")
    seen = {}

    async def capture(*args, **kwargs):
        assert "CLOUD_CODE_URL" not in kwargs["env"]
        return process.ProcessResult(exit_code=0, stdout=version + "\n")

    async def streaming(**kwargs):
        seen.update(kwargs["env"])
        raise RuntimeError("test launch failure")

    async def emit(*_args):
        pass

    monkeypatch.setattr(process, "run_capture", capture)
    monkeypatch.setattr(process, "run_streaming", streaming)
    request = ExecutionRequest(job_id="compat-test", work_dir=tmp_path,
                               system_prompt="", user_message="test",
                               tool_policy=AGY_WEB_SEARCH if search else None)
    with pytest.raises(RuntimeError, match="test launch failure"):
        await AgyCliProvider(str(executable)).execute(request, emit)
    assert ("CLOUD_CODE_URL" in seen) is enabled
    if enabled:
        url = urlsplit(seen["CLOUD_CODE_URL"])
        with pytest.raises(OSError):
            socket.create_connection((url.hostname, url.port), timeout=0.2)
