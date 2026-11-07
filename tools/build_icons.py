"""Build the dashboard's icon sprite from a local copy of Lucide (ISC licence).

The app never fetches icons at run time. To add one, put its name in ICONS and run:

    npm pack lucide-static            # in an empty folder, then extract the tarball
    python tools/build_icons.py path/to/package/icons

Templates use an icon with `{{ icon("name") }}` (see _macros.html), which points at a <symbol> in
src/stealth_app/static/icons.svg.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ICONS = [
    "arrow-left",
    "arrow-right",
    "bell",
    "building-2",
    "check",
    "chevron-down",
    "chevron-right",
    "circle-alert",
    "circle-check",
    "circle-help",
    "circle-x",
    "clock",
    "copy",
    "download",
    "eye",
    "file-check",
    "file-text",
    "flag",
    "hourglass",
    "inbox",
    "info",
    "layout-dashboard",
    "lock",
    "log-out",
    "mail",
    "mail-open",
    "moon",
    "play",
    "refresh-cw",
    "scale",
    "search",
    "send",
    "settings",
    "shield",
    "shield-check",
    "sparkles",
    "sun",
    "triangle-alert",
    "user",
    "users",
    "x",
]

OUT = Path(__file__).resolve().parents[1] / "src" / "stealth_app" / "static" / "icons.svg"
SHAPES = re.compile(r"<(path|circle|rect|line|polyline|polygon|ellipse)\b[^>]*/>")


def symbol(name: str, source: Path) -> str:
    text = (source / f"{name}.svg").read_text("utf-8")
    shapes = "".join(m.group(0).replace(" />", "/>") for m in SHAPES.finditer(text))
    if not shapes:
        raise SystemExit(f"{name}.svg has no shapes")
    return f'<symbol id="{name}" viewBox="0 0 24 24">{shapes}</symbol>'


def main() -> None:
    source = Path(sys.argv[1])
    body = "\n".join(symbol(name, source) for name in ICONS)
    OUT.write_text(
        "<!-- Lucide icons (ISC licence), https://lucide.dev. Built by tools/build_icons.py. -->\n"
        '<svg xmlns="http://www.w3.org/2000/svg" fill="none" stroke="currentColor" '
        'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">\n'
        f"{body}\n</svg>\n",
        "utf-8",
    )
    print(f"Wrote {len(ICONS)} icons to {OUT}")


if __name__ == "__main__":
    main()
