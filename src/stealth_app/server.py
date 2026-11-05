"""The local web server behind the window.

Broker replies will be shown here, so the server treats every request as possibly hostile. It
listens on 127.0.0.1 only, on a random port, and checks three things:

- the Host header names that exact address, which defeats DNS rebinding;
- a per-launch token, swapped once for a cookie so it never sits in page URLs;
- (from Phase 2.2, when forms arrive) a CSRF token on every state-changing request.
"""

from __future__ import annotations

import secrets
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from stealth import __version__

COOKIE = "stealth_session"
TOKEN_HEADER = "X-Stealth-Token"  # noqa: S105 (a header name, not a secret)
# Pages a browser may reach before it holds the session cookie.
# These check the token themselves.
OPEN_PATHS = frozenset({"/auth", "/health", "/show"})


def new_token() -> str:
    return secrets.token_urlsafe(32)


def bind_socket() -> socket.socket:
    """Reserve a random free port on loopback. Binding first means no race over the port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(16)
    return sock


class GuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, *, token: str, port: int) -> None:
        super().__init__(app)
        self.token = token
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.headers.get("host", "") not in self.allowed_hosts:
            return PlainTextResponse("Wrong host.", status_code=421)
        if request.url.path not in OPEN_PATHS:
            cookie = request.cookies.get(COOKIE, "")
            if not secrets.compare_digest(cookie, self.token):
                return PlainTextResponse(
                    "Open Stealth from its tray icon or Start menu entry.", status_code=401
                )
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response


HOME_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stealth</title>
<link rel="stylesheet" href="/static/app.css">
</head>
<body>
<h1>Stealth is running</h1>
<p class="muted">Version {version}</p>
<p>The dashboard arrives in the next update. For now, use the <code>stealth</code> command.</p>
</body>
</html>
"""


STATIC_TYPES = {".css": "text/css", ".js": "text/javascript", ".png": "image/png"}


def _has_token(request: Request, token: str) -> bool:
    return secrets.compare_digest(request.headers.get(TOKEN_HEADER, ""), token)


def create_app(token: str, port: int, on_show: Callable[[], None] | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GuardMiddleware, token=token, port=port)
    static = resources.files("stealth_app") / "static"

    @app.get("/health")
    def health(request: Request) -> JSONResponse:
        # Callers without the token learn only that something is listening.
        if not _has_token(request, token):
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": True, "app": "stealth", "version": __version__})

    @app.get("/auth")
    def auth(t: str = "") -> Response:
        if not secrets.compare_digest(t, token):
            return PlainTextResponse("This link has expired. Open Stealth again.", status_code=401)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/")
        return response

    @app.post("/show")
    def show(request: Request) -> Response:
        """A second launch asks the running agent for its window instead of opening another."""
        if not _has_token(request, token) or on_show is None:
            return PlainTextResponse("No.", status_code=403)
        on_show()
        return JSONResponse({"ok": True})

    @app.get("/static/{name}")
    def static_file(name: str) -> Response:
        entry = static / name
        suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        # Names only, never paths: no slashes reach here, and dot-files are refused.
        if name.startswith(".") or suffix not in STATIC_TYPES or not entry.is_file():
            return PlainTextResponse("Not found.", status_code=404)
        return Response(entry.read_bytes(), media_type=STATIC_TYPES[suffix])

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return HOME_PAGE.format(version=__version__)

    return app


@dataclass
class RunningServer:
    token: str
    port: int
    _server: uvicorn.Server
    _thread: threading.Thread = field(repr=False)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def login_url(self) -> str:
        return f"{self.base_url}/auth?t={self.token}"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def start_server(
    token: str | None = None, on_show: Callable[[], None] | None = None
) -> RunningServer:
    """Run the server on a background thread and return once it accepts connections."""
    token = token or new_token()
    sock = bind_socket()
    port = sock.getsockname()[1]
    config = uvicorn.Config(
        create_app(token, port, on_show),
        log_config=None,  # our scrubbed logging, not uvicorn's console handlers
        access_log=False,
        lifespan="off",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, name="stealth-server", daemon=True
    )
    thread.start()
    for _ in range(200):
        if server.started:
            break
        if not thread.is_alive():
            raise RuntimeError("the local server stopped during startup")
        threading.Event().wait(0.05)
    else:
        raise RuntimeError("the local server didn't start within 10 seconds")
    return RunningServer(token, port, server, thread)
