"""Loading and choosing brokers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

from stealth.core import paths
from stealth.core.errors import RegistryError
from stealth.core.jurisdictions import Jurisdiction
from stealth.profiles.models import Settings


@dataclass(frozen=True)
class Broker:
    id: str
    name: str
    domain: str
    region: str
    category: str
    method: str
    email: str | None
    form_url: str | None
    notes: str
    source: str
    last_verified: str
    laws: tuple[str, ...]
    tier: str
    confidence: float
    reply_domains: tuple[str, ...] = field(default=())

    @property
    def all_domains(self) -> tuple[str, ...]:
        """Every domain a genuine reply from this company might come from."""
        own = (self.email.rsplit("@", 1)[1].lower(),) if self.email else ()
        return (self.domain.lower(), *own, *(d.lower() for d in self.reply_domains))


@dataclass(frozen=True)
class Selection:
    """A broker chosen for a person, and how to approach it."""

    broker: Broker
    tier: str  # "core": a law clearly applies. "extended": only if they reach into the UK.


def _schema() -> dict[str, Any]:
    text = (resources.files("stealth.registry") / "schema.json").read_text("utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


def validate(document: Any, origin: str) -> None:
    try:
        jsonschema.validate(document, _schema())
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path)
        raise RegistryError(
            f"The broker list in {origin} is invalid at {where}: {exc.message}"
        ) from exc


def _to_broker(raw: dict[str, Any]) -> Broker:
    fields = {
        **raw,
        "laws": tuple(raw["laws"]),
        "reply_domains": tuple(raw.get("reply_domains", ())),
    }
    return Broker(**fields)


def packaged_document() -> dict[str, Any]:
    text = (resources.files("stealth.registry") / "data" / "brokers.json").read_text("utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


def current_document(home: Path | None = None) -> dict[str, Any]:
    """The newest genuine list: a signed download if it's newer, else the one in the app."""
    from stealth.registry import updater

    packaged = packaged_document()
    validate(packaged, "the app")
    downloaded = updater.cached_document(home)
    if downloaded is not None and downloaded["version"] > packaged["version"]:
        return downloaded
    return packaged


def load_registry(extra_path: Path | None = None) -> dict[str, Broker]:
    """The full registry, with the person's own `brokers_extra.json` layered over it."""
    document = current_document()
    merged = {b["id"]: b for b in document["brokers"]}
    extra = extra_path or paths.extra_brokers_file()
    if extra.exists():
        try:
            # utf-8-sig: PowerShell and some Windows editors start the file with a byte order mark.
            extras = json.loads(extra.read_text("utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise RegistryError(
                f"{extra.name} isn't valid JSON.",
                hint=f"Line {exc.lineno}: {exc.msg}",
            ) from exc
        validate(extras, extra.name)
        merged.update({b["id"]: b for b in extras["brokers"]})
    return {k: _to_broker(v) for k, v in merged.items()}


def _choose(
    brokers: dict[str, Broker],
    jurisdiction: Jurisdiction,
    *,
    skip: frozenset[str] = frozenset(),
    include_extended: bool = False,
) -> list[Selection]:
    chosen = []
    seen_contacts: set[str] = set()
    # Best entries first, so when several share an address the most reliable one represents them.
    ordered = sorted(brokers.values(), key=lambda b: (b.tier != "core", -b.confidence, b.id))
    for broker in ordered:
        if broker.id in skip:
            continue
        core = broker.tier == "core" and bool(jurisdiction.law_tags & set(broker.laws))
        if not core and not include_extended:
            continue
        # Networks of sites often share one privacy inbox. One letter covers them all, and twelve
        # identical letters to the same address would only look like spam.
        contact = broker.email.lower() if broker.email else None
        if contact and contact in seen_contacts:
            continue
        if contact:
            seen_contacts.add(contact)
        chosen.append(Selection(broker, "core" if core else "extended"))
    return sorted(chosen, key=lambda s: s.broker.id)


def select_brokers(
    brokers: dict[str, Broker], jurisdiction: Jurisdiction, settings: Settings
) -> list[Selection]:
    """Choose who to write to.

    Core means a high-confidence entry under a law this person can actually use. Everything else
    is extended: writing to it shares personal details with a company that may owe them nothing,
    so it happens only if they opt in.
    """
    return _choose(
        brokers,
        jurisdiction,
        skip=frozenset(settings.skip_brokers),
        include_extended=settings.include_extended,
    )


def counts(brokers: dict[str, Broker], jurisdiction: Jurisdiction) -> dict[str, int]:
    """The honest numbers the app and README quote: distinct contacts, not list entries."""
    default = [s.broker for s in _choose(brokers, jurisdiction)]
    everything = _choose(brokers, jurisdiction, include_extended=True)
    return {
        "total": len(everything),
        "default": len(default),
        "default_email": sum(b.method == "email" for b in default),
        "default_form": sum(b.method == "form" for b in default),
        "optional": len(everything) - len(default),
    }
