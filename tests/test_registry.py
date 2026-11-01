import json

import pytest
from tools import build_brokers

from stealth.core.errors import ProfileError, RegistryError
from stealth.core.jurisdictions import UK, for_country
from stealth.profiles.models import DEFAULT_SETTINGS, Settings
from stealth.registry import loader


def settings(**changes) -> Settings:
    return Settings(**{**DEFAULT_SETTINGS, **changes})


def raw_entry(**changes):
    entry = {
        "id": "acme-data",
        "name": "Acme Data Inc",
        "domain": "acme.example",
        "category": "data-aggregator",
        "optout": {"methods": [{"type": "email", "email_to": "privacy@acme.example"}]},
        "legal": {"ccpa": True, "gdpr": True, "state_laws": ["california"]},
        "meta": {"confidence": 0.8, "last_verified": "2026-01-01"},
    }
    return {**entry, **changes}


def test_convert_keeps_laws_and_tags_core():
    entry = build_brokers.convert(raw_entry())
    assert entry["laws"] == ["gdpr", "ccpa", "us-california"]
    assert entry["tier"] == "core"
    assert entry["method"] == "email"
    assert entry["region"] == "GLOBAL"


def test_low_confidence_is_kept_as_extended_not_dropped():
    assert build_brokers.convert(raw_entry(meta={"confidence": 0.7}))["tier"] == "extended"


@pytest.mark.parametrize(
    "change",
    [
        {"category": "financial"},
        {"category": "health"},
        {"category": "background-check"},
        {"meta": {"confidence": 0.9, "defunct": True}},
        {"optout": {"methods": []}},
        {"optout": {"methods": [{"type": "email", "email_to": "not-an-email"}]}},
        {"name": "Ancestry"},
        {"name": "Amazon Advertising"},
        {"name": "ADP"},
        {"id": "google-ads"},
        {"name": "Salesforce"},
        {"name": "Acme Payroll Services"},
        {"name": "Best Background Checks"},
        {"name": "Acme Credit Bureau"},
        {"name": "Acme Health Data"},
        {"name": "Acme Insurance Data"},
        {"name": "Experian"},
        {"name": "Equifax Inc"},
        {"domain": "transunion.com"},
    ],
)
def test_filters_reject(change):
    assert build_brokers.convert(raw_entry(**change)) is None


def test_form_only_broker_is_kept():
    entry = build_brokers.convert(
        raw_entry(optout={"methods": [{"type": "web_form", "url": "https://acme.example/out"}]})
    )
    assert entry["method"] == "form"
    assert entry["form_url"] == "https://acme.example/out"


def test_seeds_win_over_duplicates_by_domain():
    seed = {**build_brokers.convert(raw_entry()), "id": "acme-seed", "source": "seed"}
    built = build_brokers.build([raw_entry()], [seed])
    assert [b["id"] for b in built] == ["acme-seed"]


def test_seed_file_matches_the_brief():
    seeds = {s["id"]: s for s in build_brokers.load_seeds()}
    assert len(seeds) == 7
    assert seeds["experian-uk"]["email"] == "uk.dpo@experian.com"
    assert seeds["equifax-uk"]["email"] == "UKDPO@equifax.com"
    assert seeds["transunion-uk"]["email"] == "ukconsumer@transunion.com"
    assert seeds["ukphonebook"]["email"] == "dpo@simunix.com"
    assert seeds["captify"]["email"] == "dpo@captify.co.uk"
    assert seeds["192-com"]["method"] == "form"
    assert seeds["caci"]["method"] == "form"
    assert all("verified November 2026" in s["source"] for s in seeds.values())


def test_shipped_registry_passes_schema_and_contains_seeds():
    brokers = loader.load_registry(extra_path=_missing())
    assert {"experian-uk", "192-com", "captify"} <= set(brokers)
    assert all(b.email or b.form_url for b in brokers.values())


def test_shipped_registry_has_no_excluded_companies():
    for broker in loader.load_registry(extra_path=_missing()).values():
        if broker.tier and broker.source.startswith("DataPurge"):
            text = f"{broker.id} {broker.name} {broker.domain}"
            assert not build_brokers.US_DUPLICATES.search(text), broker.id


def _missing():
    from pathlib import Path

    return Path("does-not-exist.json")


def broker(id_="b", tier="core", laws=("gdpr",), method="email"):
    return loader.Broker(
        id=id_,
        name=id_,
        domain=f"{id_}.example",
        region="EU",
        category="other",
        method=method,
        email=f"p@{id_}.example" if method == "email" else None,
        form_url=None if method == "email" else "https://x.example",
        notes="",
        source="test",
        last_verified="",
        laws=laws,
        tier=tier,
        confidence=0.8 if tier == "core" else 0.5,
    )


def test_select_uses_core_brokers_under_usable_laws_only():
    brokers = {
        "uk": broker("uk", laws=("uk-gdpr",)),
        "eu": broker("eu", laws=("gdpr", "ccpa")),
        "us": broker("us", laws=("ccpa",)),
        "low": broker("low", tier="extended", laws=("gdpr",)),
    }
    chosen = loader.select_brokers(brokers, UK, settings())
    assert [s.broker.id for s in chosen] == ["eu", "uk"]
    assert {s.tier for s in chosen} == {"core"}


def test_extended_is_opt_in():
    brokers = {"uk": broker("uk"), "us": broker("us", laws=("ccpa",))}
    chosen = loader.select_brokers(brokers, UK, settings(include_extended=True))
    assert {s.broker.id: s.tier for s in chosen} == {"uk": "core", "us": "extended"}


def test_skip_list_is_respected():
    brokers = {"a": broker("a"), "b": broker("b")}
    assert [
        s.broker.id for s in loader.select_brokers(brokers, UK, settings(skip_brokers=["a"]))
    ] == ["b"]


def test_counts_are_honest():
    brokers = {
        "a": broker("a"),
        "f": broker("f", method="form"),
        "u": broker("u", laws=("ccpa",)),
    }
    assert loader.counts(brokers, UK) == {
        "total": 3,
        "default": 2,
        "default_email": 1,
        "default_form": 1,
        "optional": 1,
    }


def test_extra_file_overrides_and_adds(tmp_path):
    extra = tmp_path / "brokers_extra.json"
    entry = {
        "id": "experian-uk",
        "name": "Fixed Name",
        "domain": "experian.co.uk",
        "region": "UK",
        "category": "data-aggregator",
        "method": "email",
        "email": "new@experian.example",
        "form_url": None,
        "notes": "",
        "source": "me",
        "last_verified": "2026-11-01",
        "laws": ["uk-gdpr"],
        "tier": "core",
        "confidence": 1,
    }
    extra.write_text(json.dumps({"version": 1, "brokers": [entry, {**entry, "id": "mine"}]}))
    brokers = loader.load_registry(extra_path=extra)
    assert brokers["experian-uk"].email == "new@experian.example"
    assert "mine" in brokers


def test_bad_extra_file_gives_a_clear_error(tmp_path):
    extra = tmp_path / "brokers_extra.json"
    extra.write_text('{"version": 1, "brokers": [{"id": "Bad Id"}]}')
    with pytest.raises(RegistryError, match="invalid"):
        loader.load_registry(extra_path=extra)
    extra.write_text("{not json")
    with pytest.raises(RegistryError, match="valid JSON"):
        loader.load_registry(extra_path=extra)


def test_email_broker_without_email_fails_schema():
    doc = {
        "version": 1,
        "brokers": [
            {
                "id": "x",
                "name": "x",
                "domain": "x.example",
                "region": "UK",
                "category": "other",
                "method": "email",
                "email": None,
                "form_url": None,
                "notes": "",
                "source": "t",
                "last_verified": "",
                "laws": [],
                "tier": "core",
                "confidence": 1,
            }
        ],
    }
    with pytest.raises(RegistryError):
        loader.validate(doc, "test")


def test_all_domains_includes_contact_and_reply_domains():
    b = loader.Broker(
        id="x",
        name="x",
        domain="x.co.uk",
        region="UK",
        category="other",
        method="email",
        email="dpo@X.com",
        form_url=None,
        notes="",
        source="t",
        last_verified="",
        laws=(),
        tier="core",
        confidence=1,
        reply_domains=("y.com",),
    )
    assert b.all_domains == ("x.co.uk", "x.com", "y.com")


def test_unknown_country_is_explained():
    assert for_country("gb") is UK
    with pytest.raises(ProfileError, match="doesn't support"):
        for_country("ZZ")
