"""Work around agy 1.2.2's first-part-only Google search summary parser.

The CLI owns OAuth. A run-scoped loopback relay forwards it only to the original
Google host, with TLS verification, and removes leading empty thought parts from
successful googleSearch responses. No credentials or request/response bodies are
logged. See docs/agy-search-compat-2026-09-14.md for the observed wire contract.
"""
from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import secrets
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "daily-cloudcode-pa.googleapis.com"
MAX_BODY = 16 * 1024 * 1024
ROUTES = frozenset({
    "/v1internal:loadCodeAssist", "/v1internal:fetchAvailableModels",
    "/v1internal:fetchUserInfo", "/v1internal:fetchAdminControls",
    "/v1internal:retrieveUserQuotaSummary", "/v1internal:listExperiments",
    "/v1internal:writeTrajectoryAcls", "/v1internal:recordTrajectoryAnalytics",
    "/v1internal:generateContent", "/v1internal:streamGenerateContent?alt=sse",
})
_HOP_HEADERS = {"host", "connection", "content-length", "transfer-encoding",
                "keep-alive", "te", "trailer", "upgrade", "proxy-authorization",
                "proxy-authenticate"}


def normalize_search_response(body: bytes) -> tuple[bytes, int]:
    """Preserve genuine text, signatures, grounding and errors; never invent text."""
    try:
        payload = json.loads(body)
        response = payload.get("response") if isinstance(payload, dict) else None
        candidates = response.get("candidates") if isinstance(response, dict) else None
        if not isinstance(candidates, list) or payload.get("error"):
            return body, 0
        removed = 0
        for candidate in candidates:
            content = candidate.get("content") if isinstance(candidate, dict) else None
            parts = content.get("parts") if isinstance(content, dict) else None
            if not isinstance(parts, list):
                continue
            index = 0
            for part in parts:
                # Do not discard signatures, tool calls or newly introduced fields.
                if (not isinstance(part, dict) or set(part) != {"thought", "text"}
                        or part["thought"] is not True or part["text"] != ""):
                    break
                index += 1
            # Empty-only responses must remain failures, and thoughts are not answers.
            if (index and index < len(parts) and isinstance(parts[index], dict)
                    and parts[index].get("thought") is not True
                    and isinstance(parts[index].get("text"), str)
                    and parts[index]["text"].strip()):
                content["parts"] = parts[index:]
                removed += index
        if removed:
            return json.dumps(payload, ensure_ascii=False).encode("utf-8"), removed
    except (ValueError, UnicodeError, RecursionError):
        pass
    return body, 0


def _is_search(body: bytes) -> bool:
    try:
        request = json.loads(body)
        return any(isinstance(tool, dict) and "googleSearch" in tool
                   for tool in request["request"]["tools"])
    except (ValueError, UnicodeError, KeyError, TypeError, RecursionError):
        return False


class _Relay(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, timeout: float):
        self.prefix = "/" + secrets.token_urlsafe(32)
        self.timeout_seconds = timeout
        self.tls = ssl.create_default_context()
        self.lock = threading.Lock()
        self.clients: set[socket.socket] = set()
        self.upstreams: set[http.client.HTTPSConnection] = set()
        self.stopping = False
        self.corrected_responses = 0
        self.removed_parts = 0
        super().__init__(("127.0.0.1", 0), _Handler)

    def connection(self):
        return http.client.HTTPSConnection(UPSTREAM, context=self.tls,
                                           timeout=self.timeout_seconds)

    def stop(self):
        with self.lock:
            self.stopping = True
            sockets = list(self.clients) + [c.sock for c in self.upstreams if c.sock]
            for sock in sockets:
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)
                with contextlib.suppress(OSError):
                    sock.close()
        self.shutdown()
        self.server_close()


class _Handler(BaseHTTPRequestHandler):
    server: _Relay

    def log_message(self, *_args):
        pass  # The capability URL, OAuth and document contents must not reach logs.

    def setup(self):
        super().setup()
        self.connection.settimeout(self.server.timeout_seconds)
        with self.server.lock:
            if self.server.stopping:
                self.connection.close()
            else:
                self.server.clients.add(self.connection)

    def finish(self):
        try:
            super().finish()
        finally:
            with self.server.lock:
                self.server.clients.discard(self.connection)

    def _read_body(self) -> bytes:
        transfer = self.headers.get("Transfer-Encoding", "").lower()
        if transfer:
            if transfer != "chunked" or self.headers.get("Content-Length"):
                raise ValueError("Invalid framing")
            chunks, total = [], 0
            while True:
                line = self.rfile.readline(8193)
                if len(line) > 8192:
                    raise ValueError("Chunk header too large")
                size = int(line.split(b";", 1)[0].strip(), 16)
                total += size
                if size < 0 or total > MAX_BODY:
                    raise ValueError("Body too large")
                if not size:
                    # agy sends no trailers. Reject them rather than silently forward.
                    if self.rfile.readline(8193) != b"\r\n":
                        raise ValueError("Unexpected trailer")
                    return b"".join(chunks)
                chunk = self.rfile.read(size)
                if len(chunk) != size or self.rfile.read(2) != b"\r\n":
                    raise ValueError("Incomplete chunk")
                chunks.append(chunk)
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 <= size <= MAX_BODY:
            raise ValueError("Body too large")
        body = self.rfile.read(size)
        if len(body) != size:
            raise ValueError("Incomplete body")
        return body

    def do_POST(self):
        prefix = self.server.prefix + "/"
        path = self.path[len(self.server.prefix):] if self.path.startswith(prefix) else ""
        if (path not in ROUTES or self.headers.get("Origin")
                or not self.headers.get("Authorization", "").startswith("Bearer ")):
            self.send_error(403)
            return
        upstream = None
        response_started = False
        try:
            body = self._read_body()
            hop = _HOP_HEADERS | {h.strip().lower() for h in
                                  self.headers.get("Connection", "").split(",")}
            headers = {k: v for k, v in self.headers.items() if k.lower() not in hop}
            headers["Accept-Encoding"] = "identity"
            upstream = self.server.connection()
            with self.server.lock:
                if self.server.stopping:
                    return
                self.server.upstreams.add(upstream)
            # Fixed host and allowlisted paths; redirects are passed through, not followed.
            upstream.request("POST", path, body, headers)
            response = upstream.getresponse()
            correct = (path == "/v1internal:generateContent" and response.status == 200
                       and _is_search(body)
                       and not response.getheader("Content-Encoding")
                       and "application/json" in (response.getheader("Content-Type") or ""))
            normalized = None
            removed = 0
            if correct:
                normalized = response.read(MAX_BODY + 1)
                if len(normalized) > MAX_BODY:
                    raise ValueError("Response too large")
                normalized, removed = normalize_search_response(normalized)
                if removed:
                    with self.server.lock:
                        self.server.corrected_responses += 1
                        self.server.removed_parts += removed
            self.send_response(response.status)
            response_hop = _HOP_HEADERS | {h.strip().lower() for h in
                                          (response.getheader("Connection") or "").split(",")}
            if removed:
                response_hop |= {"etag", "content-md5", "digest"}
            for key, value in response.getheaders():
                if key.lower() not in response_hop:
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            if normalized is not None:
                self.send_header("Content-Length", str(len(normalized)))
            self.end_headers()
            response_started = True
            if normalized is not None:
                self.wfile.write(normalized)
            else:
                # SSE must reach the CLI immediately; buffering it stalls tool execution.
                while chunk := response.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (OSError, ValueError, http.client.HTTPException):
            if not response_started:
                with contextlib.suppress(OSError):
                    self.send_error(502, "Search compatibility transport failed")
        finally:
            if upstream is not None:
                upstream.close()
                with self.server.lock:
                    self.server.upstreams.discard(upstream)


@contextlib.asynccontextmanager
async def search_compatibility(*, enabled: bool, timeout: float):
    """A separate relay per execution, including cancellation and exception cleanup."""
    if not enabled:
        yield None
        return
    relay = _Relay(min(90, max(1, timeout)))
    thread = threading.Thread(target=relay.serve_forever,
                              kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    relay.url = f"http://127.0.0.1:{relay.server_port}{relay.prefix}"
    try:
        yield relay
    finally:
        await asyncio.to_thread(relay.stop)
        await asyncio.to_thread(thread.join, 1)
