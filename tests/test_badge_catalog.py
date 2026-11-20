"""Badge catalog: three families; Platinum/Diamond auto-award disabled."""

from app.services.badge_engine import PLATINUM_DIAMOND_DISABLED, SEED_DEFINITIONS


def test_families_include_premium():
    families = {spec["family"] for spec in SEED_DEFINITIONS}
    assert families == {"experience", "skill_evidence", "assessment", "premium"}


def test_platinum_diamond_disabled():
    assert PLATINUM_DIAMOND_DISABLED is True
    by_key = {spec["stable_key"]: spec for spec in SEED_DEFINITIONS}
    assert by_key["skill_platinum"]["status"] == "disabled"
    assert by_key["skill_diamond"]["status"] == "disabled"
    assert by_key["skill_platinum"]["rules"].get("disabled") is True
