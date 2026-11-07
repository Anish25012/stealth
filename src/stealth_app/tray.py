"""The tray icon and its menu."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources
from typing import Any

from PIL import Image


def icon_image() -> Image.Image:
    with (resources.files("stealth_app") / "assets" / "icon.png").open("rb") as handle:
        image = Image.open(handle)
        image.load()
    return image


def build_icon(
    on_open: Callable[[], None],
    on_quit: Callable[[], None],
    on_check: Callable[[], None] | None = None,
    status: Callable[[], str] | None = None,
) -> Any:
    import pystray  # imported late: it picks a desktop backend as it loads

    def open_item(_icon: Any, _item: Any) -> None:
        on_open()

    def check_item(_icon: Any, _item: Any) -> None:
        if on_check is not None:
            on_check()

    def quit_item(icon: Any, _item: Any) -> None:
        on_quit()
        icon.stop()

    items = []
    if status is not None:
        # Worked out each time the menu opens, so it's always current.
        items += [
            pystray.MenuItem(lambda _item: status(), None, enabled=False),
            pystray.Menu.SEPARATOR,
        ]
    items.append(pystray.MenuItem("Open Stealth", open_item, default=True))
    if on_check is not None:
        items.append(pystray.MenuItem("Check now", check_item))
    items += [pystray.Menu.SEPARATOR, pystray.MenuItem("Quit Stealth", quit_item)]
    return pystray.Icon("stealth", icon_image(), "Stealth", pystray.Menu(*items))
