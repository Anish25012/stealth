"""The style guide: every component of the design system, in light and dark, on one page.

Only for development. It's installed when STEALTH_DEV=1 is set, so an installed copy never has
it. Nothing on it touches a profile: the data is made up in the template.
"""

from __future__ import annotations

import os
import re
from importlib import resources

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from stealth_app import dashboard, views


def enabled() -> bool:
    return os.environ.get("STEALTH_DEV") == "1"


def icon_names() -> list[str]:
    sprite = (resources.files("stealth_app") / "static" / "icons.svg").read_text("utf-8")
    return re.findall(r'<symbol id="([\w-]+)"', sprite)


def install(app: FastAPI) -> None:
    @app.get("/styleguide", response_class=HTMLResponse)
    def styleguide(request: Request) -> Response:
        return dashboard.render(
            request,
            "styleguide.html",
            title="Style guide",
            icons=icon_names(),
            statuses=[views.STATUS[s] for s in views.STATUS],
        )
