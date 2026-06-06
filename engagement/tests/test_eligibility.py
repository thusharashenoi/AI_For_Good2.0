from datetime import datetime, timedelta, timezone

from shared import eligibility_rules as r


def test_abo_universal_donor():
    for g in r.ALL_BLOOD_GROUPS:
        assert r.is_compatible("O-", g)


def test_abo_universal_recipient():
    for g in r.ALL_BLOOD_GROUPS:
        assert r.is_compatible(g, "AB+")


def test_abo_incompatible():
    assert not r.is_compatible("A+", "O+")
    assert not r.is_compatible("B+", "A+")
    assert not r.is_compatible("AB+", "O-")


def test_compatible_donor_groups_for_opos():
    groups = set(r.compatible_donor_groups("O+"))
    assert groups == {"O-", "O+"}


def test_normalize_blood_group():
    assert r.normalize_blood_group("o positive") == "O+"
    assert r.normalize_blood_group(" b- ") == "B-"
    assert r.normalize_blood_group("xyz") is None


def test_cooldown_window():
    recent = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    old = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%Y-%m-%d")
    assert r.in_cooldown(recent)
    assert not r.in_cooldown(old)
    assert r.days_until_eligible(recent) == 60
    assert r.days_until_eligible(old) == 0


def test_permanent_deferral_diabetes():
    res = r.evaluate_deferral({"diabetes": True})
    assert res["eligible"] is False
    assert res["permanent"] is True


def test_temporary_deferral_picks_longest():
    res = r.evaluate_deferral({"recentFever": True, "recentTattoo": True})
    # tattoo (182d) dominates fever (14d)
    assert res["deferral_days"] == 182
    assert res["eligible"] is False


def test_no_flags_eligible():
    res = r.evaluate_deferral({"recentFever": False})
    assert res["eligible"] is True


def test_derive_status_eligible_first_timer():
    donor = {"medicalFlags": {}, "lastDonationDate": None}
    assert r.derive_eligibility_status(donor)["status"] == "eligible"


def test_derive_status_cooldown():
    recent = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    donor = {"medicalFlags": {}, "lastDonationDate": recent}
    out = r.derive_eligibility_status(donor)
    assert out["status"] == "cooldown"
    assert out["cooldownEndsAt"] is not None


def test_age_weight_gates():
    assert r.check_age(17) == (False, "underage")
    assert r.check_age(70) == (False, "overage")
    assert r.check_age(30) == (True, None)
    assert r.check_weight(40) == (False, "underweight")
    assert r.check_weight(60) == (True, None)
