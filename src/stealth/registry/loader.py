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


def load_registry(extra_path: Path | None = None) -> dict[str, Broker]:
    """The full registry, with the person's own `brokers_extra.json` layered over it."""
    document = packaged_document()
    validate(document, "the app")
    merged = {b["id"]: b for b in document["brokers"]}
    extra = extra_path or paths.extra_brokers_file()
    if extra.exists():
        try:
            extras = json.loads(extra.read_text("utf-8"))
        except json.JSONDecodeError as exc:
            raise RegistryError(
                f"{extra.name} isn't valid JSON.",
                hint=f"Line {exc.lineno}: {exc.msg}",
            ) from exc
        validate(extras, extra.name)
        merged.update({b["id"]: b for b in extras["brokers"]})
    return {k: _to_broker(v) for k, v in merged.items()}


def select_brokers(
    brokers: dict[str, Broker], jurisdiction: Jurisdiction, settings: Settings
) -> list[Selection]:
    """Choose who to write to.

    Core means a high-confidence entry under a law this person can actually use. Everything else
    is extended: writing to it shares personal details with a company that may owe them nothing,
    so it happens only if they opt in.
    """
    chosen = []
    for broker in sorted(brokers.values(), key=lambda b: b.id):
        if broker.id in settings.skip_brokers:
            continue
        applies = bool(jurisdiction.law_tags & set(broker.laws))
        if broker.tier == "core" and applies:
            chosen.append(Selection(broker, "core"))
        elif settings.include_extended:
            chosen.append(Selection(broker, "extended"))
    return chosen


def counts(brokers: dict[str, Broker], jurisdiction: Jurisdiction) -> dict[str, int]:
    """The honest numbers the app and README quote."""
    core = [b for b in brokers.values() if b.tier == "core" and jurisdiction.law_tags & set(b.laws)]
    return {
        "total": len(brokers),
        "default": len(core),
        "default_email": sum(b.method == "email" for b in core),
        "default_form": sum(b.method == "form" for b in core),
        "optional": len(brokers) - len(core),
    }
