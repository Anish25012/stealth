from stealth.registry.loader import Broker


def email_broker(id_="acme", *, category="data-aggregator", tier="core", laws=("uk-gdpr",)):
    return Broker(
        id=id_,
        name=f"{id_} Ltd",
        domain=f"{id_}.example",
        region="UK",
        category=category,
        method="email",
        email=f"privacy@{id_}.example",
        form_url=None,
        notes="",
        source="test",
        last_verified="2026-11-01",
        laws=laws,
        tier=tier,
        confidence=1.0 if tier == "core" else 0.5,
    )


def form_broker(id_="formco"):
    return Broker(
        id=id_,
        name=f"{id_} Ltd",
        domain=f"{id_}.example",
        region="UK",
        category="people-search",
        method="form",
        email=None,
        form_url=f"https://{id_}.example/remove",
        notes="note",
        source="test",
        last_verified="2026-11-01",
        laws=("uk-gdpr",),
        tier="core",
        confidence=1.0,
    )


def registry(*brokers):
    return {b.id: b for b in brokers}
