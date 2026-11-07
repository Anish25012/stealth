"""Phone view: a read-only look at Stealth from a phone on the same Wi-Fi.

The desktop server never leaves 127.0.0.1. When the person turns phone view on in Settings, a
second, separate server starts on this computer's Wi-Fi address with:

- HTTPS, using a certificate made on this computer. A LAN address can't get a trusted one, so the
  phone's browser warns once; the fingerprint shown in Settings lets the person check it.
- A six-digit one-time code, shown only on the desktop. It works once, for one phone. Five wrong
  guesses end phone view.
- A time limit: it stops by itself after 30 minutes, or when the app quits.
- No way to change anything: every route is a GET except entering the code. No letters, no
  replies' contents, no settings.

Service workers need a trusted certificate, so "install to home screen" only half works. The
proper mobile app is Phase 4.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import secrets
import socket
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from stealth import __version__
from stealth.core import clock, paths
from stealth.core.errors import StealthError
from stealth_app import views
from stealth_app.dashboard import (
    TEMPLATES,
    State,
    opened,
    read_form,
    redirect,
    static_response,
)

log = logging.getLogger("stealth.app.phone")

LIFETIME = timedelta(minutes=30)
MAX_TRIES = 5
COOKIE = "stealth_phone"
CERT_DAYS = 365


# The computer's address on the network ---------------------------------------------------


def lan_address() -> str | None:
    """This computer's address on the local network, or None if it isn't on one.

    Connecting a UDP socket sends nothing; it only asks the OS which interface it would use.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed anywhere real
            address = str(probe.getsockname()[0])
    except OSError:
        return None
    ip = ipaddress.ip_address(address)
    return address if ip.is_private and not ip.is_loopback else None


# The certificate -------------------------------------------------------------------------


@dataclass(frozen=True)
class Certificate:
    cert: Path
    key: Path
    fingerprint: str  # SHA-256, as browsers show it: "AB:CD:..."


def _fingerprint(pem: bytes) -> str:
    der = x509.load_pem_x509_certificate(pem).public_bytes(serialization.Encoding.DER)
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def certificate(folder: Path, ip: str) -> Certificate:
    """The certificate for this address, made once and reused so the phone warns only once."""
    paths.ensure_private_dir(folder)
    cert_path, key_path = folder / "phone-cert.pem", folder / "phone-key.pem"
    now = datetime.now(UTC)
    if cert_path.exists() and key_path.exists():
        existing = x509.load_pem_x509_certificate(cert_path.read_bytes())
        names = existing.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        ips = [str(a) for a in names.value.get_values_for_type(x509.IPAddress)]
        if ip in ips and existing.not_valid_after_utc - now > timedelta(days=1):
            return Certificate(cert_path, key_path, _fingerprint(cert_path.read_bytes()))
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Stealth on this computer")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=CERT_DAYS))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([x509.OID_SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_path.write_bytes(pem)
    return Certificate(cert_path, key_path, _fingerprint(pem))


# One run of phone view -------------------------------------------------------------------


@dataclass
class PhoneView:
    ip: str
    port: int
    code: str
    started: datetime
    fingerprint: str = ""
    session: str | None = None  # the one phone that entered the code
    tries: int = 0
    stopped: bool = False
    _server: uvicorn.Server | None = field(default=None, repr=False)
    _timer: threading.Timer | None = field(default=None, repr=False)

    @property
    def url(self) -> str:
        return f"https://{self.ip}:{self.port}"

    @property
    def ends(self) -> datetime:
        return self.started + LIFETIME

    def active(self) -> bool:
        return not self.stopped and clock.now() < self.ends

    def stop(self) -> None:
        """End phone view. Safe to call from one of its own requests: it doesn't wait."""
        self.stopped = True
        if self._timer is not None:
            self._timer.cancel()
        if self._server is not None:
            self._server.should_exit = True

    @property
    def ends_text(self) -> str:
        return f"{self.ends.astimezone():%H:%M}"


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def start(state: State, home: Path | None = None, ip: str | None = None) -> PhoneView:
    """Start phone view, replacing any earlier run."""
    if state.phone is not None:
        state.phone.stop()
        state.phone = None
    ip = ip or lan_address()
    if ip is None:
        raise StealthError(
            "This computer doesn't seem to be on a home network.",
            hint="Connect to the same Wi-Fi as your phone, then try again.",
        )
    cert = certificate((home or paths.app_home()) / "phone", ip)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((ip, 0))
    except OSError as exc:
        sock.close()
        raise StealthError(f"Couldn't listen on {ip}.", hint=str(exc)) from exc
    sock.listen(16)
    view = PhoneView(ip, sock.getsockname()[1], new_code(), clock.now(), cert.fingerprint)
    app = create_phone_app(view, state)
    config = uvicorn.Config(
        app,
        log_config=None,
        access_log=False,
        lifespan="off",
        ssl_certfile=str(cert.cert),
        ssl_keyfile=str(cert.key),
    )
    server = uvicorn.Server(config)
    view._server = server
    threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, name="stealth-phone", daemon=True
    ).start()
    view._timer = threading.Timer(LIFETIME.total_seconds(), view.stop)
    view._timer.daemon = True
    view._timer.start()
    state.phone = view
    return view


# The phone's pages -----------------------------------------------------------------------


class PhoneGuard(BaseHTTPMiddleware):
    def __init__(self, app: Any, *, view: PhoneView) -> None:
        super().__init__(app)
        self.view = view

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        view = self.view
        if request.headers.get("host", "") != f"{view.ip}:{view.port}":
            return PlainTextResponse("Wrong host.", status_code=421)
        if not view.active():
            return PlainTextResponse(
                "Phone view has ended. Turn it on again in Stealth's Settings.", status_code=410
            )
        if request.method != "GET" and request.url.path != "/code":
            return PlainTextResponse("Phone view can't change anything.", status_code=405)
        public = request.url.path in ("/", "/code") or request.url.path.startswith("/static/")
        if not public:
            cookie = request.cookies.get(COOKIE, "")
            if view.session is None or not secrets.compare_digest(cookie, view.session):
                return RedirectResponse("/", status_code=303)
        try:
            response = await call_next(request)
        except Exception:
            log.exception("phone page failed: %s", request.url.path)
            response = PlainTextResponse("Something went wrong.", status_code=500)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Strict-Transport-Security"] = "max-age=0"  # it's a LAN address
        return response


def _page(view: PhoneView, template: str, status: int = 200, **context: Any) -> HTMLResponse:
    html = TEMPLATES.get_template(template).render(version=__version__, view=view, **context)
    return HTMLResponse(html, status_code=status)


def create_phone_app(view: PhoneView, state: State) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(PhoneGuard, view=view)

    def signed_in(request: Request) -> bool:
        cookie = request.cookies.get(COOKIE, "")
        return view.session is not None and secrets.compare_digest(cookie, view.session)

    @app.get("/", response_class=HTMLResponse)
    def code_page(request: Request) -> Response:
        if signed_in(request):
            return redirect("/home")
        return _page(view, "phone/code.html", title="Enter the code")

    @app.post("/code")
    async def enter_code(request: Request) -> Response:
        origin = request.headers.get("origin")
        if origin is not None and origin != view.url:
            return PlainTextResponse("Wrong origin.", status_code=403)
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        given = "".join(ch for ch in form.get("code", [""])[0] if ch.isdigit())
        if view.session is not None:
            return _page(
                view,
                "phone/code.html",
                403,
                title="Enter the code",
                error="Another phone already used this code. Turn phone view off and on again "
                "in Stealth to get a new one.",
            )
        if not secrets.compare_digest(given, view.code):
            view.tries += 1
            if view.tries >= MAX_TRIES:
                view.stop()
                return PlainTextResponse(
                    "Too many wrong codes, so phone view has ended.", status_code=403
                )
            left = MAX_TRIES - view.tries
            return _page(
                view,
                "phone/code.html",
                403,
                title="Enter the code",
                error=f"That code isn't right. {left} tr{'y' if left == 1 else 'ies'} left.",
            )
        view.session = secrets.token_urlsafe(32)
        response = redirect("/home")
        response.set_cookie(
            COOKIE, view.session, httponly=True, secure=True, samesite="strict", path="/"
        )
        return response

    @app.get("/home", response_class=HTMLResponse)
    def home(request: Request) -> Response:
        with opened(state) as profile:
            if profile is None:
                return _page(view, "phone/home.html", title="Stealth", summary=None)
            groups = views.grouped(views.rows(profile))
            return _page(
                view,
                "phone/home.html",
                title="Stealth",
                name=profile.person().display_name,
                summary=views.summary(groups),
                groups=groups,
                last_run=views.last_run(profile.settings()),
            )

    @app.get("/companies", response_class=HTMLResponse)
    def companies(request: Request) -> Response:
        with opened(state) as profile:
            groups = views.grouped(views.rows(profile)) if profile else {}
            return _page(view, "phone/companies.html", title="Companies", groups=groups)

    @app.get("/companies/{broker_id}", response_class=HTMLResponse)
    def company(request: Request, broker_id: str) -> Response:
        with opened(state) as profile:
            row = (
                next((r for r in views.rows(profile) if r.case.broker_id == broker_id), None)
                if profile
                else None
            )
            if profile is None or row is None:
                return PlainTextResponse("Not found.", status_code=404)
            return _page(
                view,
                "phone/company.html",
                title=row.case.broker_name,
                row=row,
                timeline=views.timeline(profile, row.case),
            )

    @app.get("/static/{name}")
    def static_file(name: str) -> Response:
        return static_response(name)

    return app


# The desktop's controls ------------------------------------------------------------------


def install(app: FastAPI, state: State) -> None:
    @app.post("/phone/start")
    async def phone_start(request: Request) -> Response:
        await read_form(request)
        start(state)
        return redirect("/settings#phone-h")

    @app.post("/phone/stop")
    async def phone_stop(request: Request) -> Response:
        await read_form(request)
        if state.phone is not None:
            state.phone.stop()
            state.phone = None
        return redirect("/settings#phone-h")
