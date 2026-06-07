from lambdas.matching_engine import scoring, eligibility, graph
from conftest import DEMO_PHONE


REQUEST = {
    "requestId": "req1", "patientId": "p1", "bloodGroup": "A+",
    "hospitalLat": 17.4, "hospitalLng": 78.48, "area": "Madhapur",
}


def _donor(donor_id, bg, **kw):
    base = {"donorId": donor_id, "bloodGroup": bg, "name": f"D {donor_id}",
            "phone": DEMO_PHONE, "registrationStatus": "complete",
            "consentGiven": True, "medicalFlags": {}, "lastDonationDate": None}
    base.update(kw)
    return base


def test_incompatible_scores_zero_compat():
    s = scoring.score_donor(_donor("1", "B+"), REQUEST)
    assert s["components"]["compatibility"] == 0.0


def test_exact_match_beats_universal():
    exact = scoring.score_donor(_donor("1", "A+", lat=17.4, lng=78.48), REQUEST)
    universal = scoring.score_donor(_donor("2", "O-", lat=17.4, lng=78.48), REQUEST)
    assert exact["components"]["compatibility"] > universal["components"]["compatibility"]


def test_proximity_increases_score():
    near = scoring.score_donor(_donor("1", "A+", lat=17.4, lng=78.48), REQUEST)
    far = scoring.score_donor(_donor("2", "A+", lat=18.5, lng=79.5), REQUEST)
    assert near["score"] > far["score"]


def test_ranking_orders_desc():
    donors = [_donor("1", "B+"), _donor("2", "A+", lat=17.4, lng=78.48),
              _donor("3", "O+", lat=17.41, lng=78.49)]
    ranked = scoring.rank_donors(donors, REQUEST)
    scores = [d["score"] for d in ranked]
    assert scores == sorted(scores, reverse=True)
    assert ranked[0]["bloodGroup"] == "A+"


def test_eligibility_filter_excludes_incompatible_and_partial():
    donors = [
        _donor("1", "A+", lat=17.4, lng=78.48),
        _donor("2", "B+"),                                   # incompatible
        _donor("3", "A+", registrationStatus="partial"),     # incomplete
        _donor("4", "A+", consentGiven=False),               # no consent
    ]
    eligible = eligibility.filter_eligible(donors, REQUEST)
    assert {d["donorId"] for d in eligible} == {"1"}


def test_graph_coverage():
    donors = [_donor("1", "A+", lat=17.4, lng=78.48, area="Madhapur"),
              _donor("2", "O-", lat=17.41, lng=78.49, area="Madhapur")]
    patients = [{"patientId": "p1", "bloodGroup": "A+", "lat": 17.4, "lng": 78.48,
                 "area": "Madhapur"}]
    g = graph.build_graph(donors, patients)
    cov = graph.coverage_score(g, "p1", {"1", "2"})
    assert cov["reachable"] >= 2
    assert cov["available"] >= 2
