"""Tests for slug deduplication fold-invariants across domain variants and entity suffixes."""
from app.sourcing.normalize import canonicalize_name, entity_slug


def test_slug_folding_collapses_entity_suffixes_and_domains():
    # Entity suffix variants of the same company
    slug1 = entity_slug("Acme Technologies Inc")
    slug2 = entity_slug("Acme Technologies Corp")
    slug3 = entity_slug("Acme Technologies LLC")
    assert slug1 == slug2 == slug3 == "acme_technologies"

    # International entity suffixes
    slug4 = entity_slug("Nordic Software Oy")
    slug5 = entity_slug("Nordic Software AB")
    slug6 = entity_slug("Nordic Software Sp. z o.o.")
    assert slug4 == slug5 == slug6 == "nordic_software"

    # Domain folding in canonicalize_name
    c1 = canonicalize_name("Acme", domain="acme.com")
    c2 = canonicalize_name("Acme Inc", domain="acme.io")
    assert c1 == c2 == "acme"
