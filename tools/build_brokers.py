"""Build Stealth's broker registry from the MIT-licensed DataPurge registry plus our UK seeds.

    python tools/build_brokers.py                     # download a pinned DataPurge commit
    python tools/build_brokers.py --source ./datapurge  # use a local checkout

Every broker in a kept category survives, tagged with the laws it accepts requests under and a
confidence tier. Which brokers a person actually writes to is decided at run time from their
jurisdiction: see `stealth.registry.loader.select_brokers`.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import tarfile
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import yaml

DATAPURGE_REPO = "puurpl/datapurge"
DATAPURGE_REF = "da1e9bb"
SEED_FILE = Path(__file__).with_name("seed_uk.json")
OUTPUT = Path(__file__).parent.parent / "src/stealth/registry/data/brokers.json"

KEEP_CATEGORIES = {
    "data-aggregator",
    "marketing-list",
    "location-tracking",
    "people-search",
    "social-scraper",
    "public-records",
    "other",
}
CONFIDENCE_CORE = 0.75

# Erasing from these could break something the person relies on.
EXCLUDED_WORDS = re.compile(
    r"\b(adp|ancestry|amazon|google|salesforce|"
    r"payroll|background[ -]?checks?|credit|health|healthcare|insurance|insurer)\b",
    re.IGNORECASE,
)
# The US arms of the credit agencies, which the UK seeds already cover properly.
US_DUPLICATES = re.compile(r"\b(experian|equifax|transunion)\b", re.IGNORECASE)
EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def contact_details(raw: dict[str, Any]) -> tuple[str | None, str | None]:
    """The first usable (email, form_url) from a DataPurge entry."""
    email = form = None
    for method in (raw.get("optout") or {}).get("methods") or []:
        if method.get("type") == "email" and not email:
            candidate = str(method.get("email_to") or "").strip()
            email = candidate if EMAIL.match(candidate) else None
        elif method.get("type") == "web_form" and not form:
            form = method.get("url")
    return email, form


def laws_of(raw: dict[str, Any]) -> list[str]:
    legal = raw.get("legal") or {}
    tags = []
    if legal.get("gdpr"):
        tags.append("gdpr")
    if legal.get("ccpa"):
        tags.append("ccpa")
    return tags + [f"us-{s}" for s in legal.get("state_laws") or []]


def region_of(laws: list[str]) -> str:
    gdpr, us = "gdpr" in laws, any(x == "ccpa" or x.startswith("us-") for x in laws)
    if gdpr and us:
        return "GLOBAL"
    if gdpr:
        return "EU"
    return "US" if us else "UNKNOWN"


def convert(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Turn one DataPurge entry into a registry entry, or None if the filters reject it."""
    meta = raw.get("meta") or {}
    identity = f"{raw.get('id', '')} {raw.get('name', '')} {raw.get('domain', '')}"
    email, form = contact_details(raw)
    if (
        raw.get("category") not in KEEP_CATEGORIES
        or meta.get("defunct")
        or not (email or form)
        or EXCLUDED_WORDS.search(identity.replace("-", " "))
        or US_DUPLICATES.search(identity)
    ):
        return None
    confidence = float(meta.get("confidence") or 0)
    laws = laws_of(raw)
    return {
        "id": raw["id"],
        "name": raw["name"],
        "domain": raw["domain"],
        "reply_domains": [],
        "region": region_of(laws),
        "category": raw["category"],
        "method": "email" if email else "form",
        "email": email,
        "form_url": form,
        "notes": "",
        "source": f"DataPurge ({DATAPURGE_REPO}@{DATAPURGE_REF})",
        "last_verified": str(meta.get("last_verified") or ""),
        "laws": laws,
        "tier": "core" if confidence >= CONFIDENCE_CORE else "extended",
        "confidence": confidence,
    }


def load_seeds() -> list[dict[str, Any]]:
    seeds = json.loads(SEED_FILE.read_text("utf-8"))
    return [
        {
            **seed,
            "last_verified": "2026-11-01",
            "laws": ["uk-gdpr"],
            "tier": "core",
            "confidence": 1.0,
        }
        for seed in seeds
    ]


def build(raw_entries: list[dict[str, Any]], seeds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge seeds over converted entries. Seeds win, matched on id or domain."""
    taken_ids = {s["id"] for s in seeds}
    taken_domains = {s["domain"] for s in seeds}
    brokers = list(seeds)
    for raw in raw_entries:
        entry = convert(raw)
        if entry and entry["id"] not in taken_ids and entry["domain"] not in taken_domains:
            brokers.append(entry)
            taken_ids.add(entry["id"])
            taken_domains.add(entry["domain"])
    return sorted(brokers, key=lambda b: b["id"])


def read_directory(root: Path) -> list[dict[str, Any]]:
    paths = sorted((root / "brokers").glob("*/*.yaml"))
    return [yaml.safe_load(p.read_text("utf-8")) for p in paths]


def read_tarball(ref: str) -> list[dict[str, Any]]:
    url = f"https://codeload.github.com/{DATAPURGE_REPO}/tar.gz/{ref}"
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read()
    entries = []
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in sorted(archive.getmembers(), key=lambda m: m.name):
            if re.search(r"/brokers/[^/]+/[^/]+\.yaml$", member.name):
                handle = archive.extractfile(member)
                if handle:
                    entries.append(yaml.safe_load(handle.read()))
    return entries


def summarise(brokers: list[dict[str, Any]]) -> dict[str, int]:
    uk = {"gdpr", "uk-gdpr"}
    core_uk = [b for b in brokers if b["tier"] == "core" and uk & set(b["laws"])]
    return {
        "total": len(brokers),
        "core": sum(b["tier"] == "core" for b in brokers),
        "extended": sum(b["tier"] == "extended" for b in brokers),
        "uk_default": len(core_uk),
        "uk_default_email": sum(b["method"] == "email" for b in core_uk),
        "uk_default_form": sum(b["method"] == "form" for b in core_uk),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="local DataPurge checkout")
    parser.add_argument("--ref", default=DATAPURGE_REF, help="DataPurge commit to download")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    raw = read_directory(args.source) if args.source else read_tarball(args.ref)
    brokers = build(raw, load_seeds())
    registry = {
        "version": int(date.today().strftime("%Y%m%d")),  # noqa: DTZ011
        "brokers": brokers,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(registry, indent=1, ensure_ascii=False) + "\n", "utf-8")
    print(f"read {len(raw)} DataPurge entries")
    print(json.dumps(summarise(brokers), indent=2))
    print(Counter(b["category"] for b in brokers).most_common())


if __name__ == "__main__":
    main()
