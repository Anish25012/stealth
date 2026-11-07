"""The local web server behind the window.

Broker replies will be shown here, so the server treats every request as possibly hostile. It
listens on 127.0.0.1 only, on a random port, and checks three things:

- the Host header names that exact address, which defeats DNS rebinding;
- a per-launch token, swapped once for a cookie so it never sits in page URLs;
- a CSRF token on every state-changing request (see `dashboard.read_form`).

Errors never show a traceback. Expected failures get their plain-English message; anything else
gets a friendly page with details, scrubbed of personal data, that the person can copy to a helper.
"""

from __future__ import annotations

import logging
import platform
import secrets
import socket
import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import PurePath
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from stealth import __version__
from stealth.core.errors import StealthError
from stealth.core.logging_scrub import scrub
from stealth_app import dashboard, styleguide
from stealth_app.workspace import Workspace

log = logging.getLogger("stealth.app.server")

COOKIE = "stealth_session"
TOKEN_HEADER = "X-Stealth-Token"  # noqa: S105 (a header name, not a secret)
# Pages a browser may reach before it holds the session cookie.
# These check the token themselves.
OPEN_PATHS = frozenset({"/auth", "/health", "/show", "/quit"})


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
        try:
            response = await call_next(request)
        except Exception as exc:
            response = error_page(request, exc)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response


STATIC_TYPES = {
    ".css": "text/css",
    ".js": "text/javascript",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


def diagnostics(exc: BaseException) -> str:
    """What a helper needs to see, with personal data scrubbed out. Never local variables."""
    frames = traceback.extract_tb(exc.__traceback__)[-8:]
    lines = [
        f"Stealth {__version__}",
        f"{platform.platform()}, Python {platform.python_version()}",
        f"Installed build: {'yes' if getattr(sys, 'frozen', False) else 'no'}",
        f"Error: {type(exc).__name__}: {exc}",
        "Where:",
        # File names only: full paths include the person's user name.
        *(f"  {PurePath(f.filename).name}:{f.lineno} in {f.name}" for f in frames),
    ]
    return scrub("\n".join(lines))


def error_page(request: Request, exc: BaseException) -> Response:
    if isinstance(exc, StealthError):
        status = 403 if isinstance(exc, dashboard.CsrfError) else 400
        heading, message, hint, details = "That didn't work", exc.message, exc.hint, ""
    else:
        log.exception("page failed: %s", request.url.path, exc_info=exc)
        status, heading = 500, "Something went wrong"
        message = "Stealth hit a problem it didn't expect. Your data is safe, and nothing was sent."
        hint, details = (
            "Try again. If it keeps happening, copy the details below.",
            diagnostics(exc),
        )
    try:
        return dashboard.render(
            request,
            "error.html",
            status,
            title=heading,
            heading=heading,
            message=message,
            hint=hint,
            details=details,
        )
    except Exception:  # the error page itself failed: fall back to plain text
        log.exception("error page failed")
        return PlainTextResponse(f"{heading}. {message}", status_code=status)


def _has_token(request: Request, token: str) -> bool:
    return secrets.compare_digest(request.headers.get(TOKEN_HEADER, ""), token)


def create_app(
    token: str,
    port: int,
    on_show: Callable[[], None] | None = None,
    workspace: Workspace | None = None,
    on_quit: Callable[[], None] | None = None,
) -> FastAPI:
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

    @app.post("/quit")
    def quit_app(request: Request) -> Response:
        """The installer asks a running copy to quit, so it can replace the files it holds open."""
        if not _has_token(request, token) or on_quit is None:
            return PlainTextResponse("No.", status_code=403)
        on_quit()
        return JSONResponse({"ok": True})

    @app.get("/static/{name}")
    def static_file(name: str) -> Response:
        entry = static / name
        suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
        # Names only, never paths: no slashes reach here, and dot-files are refused.
        if name.startswith(".") or suffix not in STATIC_TYPES or not entry.is_file():
            return PlainTextResponse("Not found.", status_code=404)
        return Response(entry.read_bytes(), media_type=STATIC_TYPES[suffix])

    dashboard.install(app, workspace)
    if styleguide.enabled():
        styleguide.install(app)
    return app


@dataclass
class RunningServer:
    token: str
    port: int
    dashboard: dashboard.State
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
        # A demo left open when the app quits still gets its folder and mail servers cleaned up.
        demo, self.dashboard.demo = self.dashboard.demo, None
        if demo is not None:
            demo.close()


def start_server(
    token: str | None = None,
    on_show: Callable[[], None] | None = None,
    workspace: Workspace | None = None,
    on_quit: Callable[[], None] | None = None,
) -> RunningServer:
    """Run the server on a background thread and return once it accepts connections."""
    token = token or new_token()
    sock = bind_socket()
    port = sock.getsockname()[1]
    app = create_app(token, port, on_show, workspace, on_quit)
    config = uvicorn.Config(
        app,
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
    return RunningServer(token, port, app.state.dashboard, server, thread)
