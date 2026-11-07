"""Made-up websites for the demo and the tests, so nothing reaches a real company.

`LocalPeopleSearch` is a tiny people-search site on this computer: search a name and it lists
"Alex Example" in Leeds, the demo's person. `demo_fetch` answers website lookups for a few `.test`
companies with privacy policies written the way real ones are, quirks included.
"""

from __future__ import annotations

import html
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Self
from urllib.parse import parse_qs, urlsplit

from stealth.core.errors import StealthError
from stealth.custom.policy_fetch import Page

LOCALHOST = "127.0.0.1"

SEARCH_PAGE = """<!doctype html><html><head><title>Find-a-Neighbour: people search</title></head>
<body style="font-family:sans-serif;max-width:40rem;margin:2rem auto">
<h1>Find-a-Neighbour</h1><p>Search the UK's friendliest people directory.</p>
<form action="/search"><input name="name" placeholder="Full name" autofocus>
<input name="town" placeholder="Town"><button>Search</button></form>
<p style="color:#666">Demo site: it runs on your computer and holds made-up people only.</p>
</body></html>"""

RESULTS_PAGE = """<!doctype html><html><head><title>{name}: Find-a-Neighbour</title></head>
<body style="font-family:sans-serif;max-width:40rem;margin:2rem auto">
<h1>Results for {name}</h1>{rows}
<p><a href="/">New search</a></p></body></html>"""

LISTING = """<div style="border:1px solid #ccc;padding:1rem;margin:1rem 0">
<h2>Alex Example, 41</h2><p>12 Sample Street, Leeds LS1 1AA</p><p>Phone: 0113 496 0000</p>
<p>Lived with: J. Example</p></div>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # the name http.server calls
        parts = urlsplit(self.path)
        if parts.path == "/search":
            query = parse_qs(parts.query)
            name = (query.get("name") or [""])[0].strip()
            rows = LISTING if "alex" in name.lower() else "<p>No one found by that name.</p>"
            body = RESULTS_PAGE.format(name=html.escape(name or "everyone"), rows=rows)
        else:
            body = SEARCH_PAGE
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        pass


class LocalPeopleSearch:
    def __init__(self) -> None:
        self._server = ThreadingHTTPServer((LOCALHOST, 0), _Handler)
        self.port = self._server.server_address[1]
        self.host = f"{LOCALHOST}:{self.port}"
        self.url = f"http://{self.host}/"

    def __enter__(self) -> Self:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._server.shutdown()
        self._server.server_close()


# Company websites for "Add a company" -----------------------------------------------------

DEMO_SITES: dict[str, str] = {
    "https://shopwise.test/": """<html><head><title>ShopWise | Deals every day</title></head>
<body><nav><a href="/about">About</a> <a href="/legal/privacy-notice">Privacy</a>
<a href="/contact">Contact</a></nav><p>Sign up for our newsletter!</p>
<footer>Questions? hello@shopwise.test</footer></body></html>""",
    "https://shopwise.test/legal/privacy-notice": """<html><head><title>Privacy notice</title>
</head><body><h1>Privacy notice</h1><p>For press enquiries contact press@shopwise.test.</p>
<h2>Your rights</h2><p>You can ask us to delete your personal data. Contact our Data Protection
Officer at dpo [at] shopwise [dot] test, or write to ShopWise Ltd, 1 High Street, Leeds.</p>
<p>Our parent company's lawyers: legal@bigholding.test.</p></body></html>""",
    "https://quietlist.test/": """<html><head><title>QuietList</title></head><body>
<p>We build marketing lists. <a href="/privacy">Privacy policy</a></p></body></html>""",
    "https://quietlist.test/privacy": """<html><head><title>Privacy</title></head><body>
<p>To exercise your data protection rights, email
<a href="mailto:privacy@quietlist.test">our privacy team</a>.</p></body></html>""",
}


def demo_fetch(url: str) -> Page:
    """Website lookups in the demo: canned `.test` sites only, and nothing ever leaves."""
    host = urlsplit(url).hostname or ""
    if not host.endswith(".test"):
        raise StealthError(
            "The demo only knows made-up websites.",
            hint="Try shopwise.test or quietlist.test, or leave the demo to look up a real one.",
        )
    if url.endswith("/robots.txt"):
        return Page(url, 404, "")
    body = DEMO_SITES.get(url)
    return Page(url, 200, body) if body is not None else Page(url, 404, "")
