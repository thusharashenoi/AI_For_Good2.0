"""Tests for eligibility checklist tools and check_eligibility completeness."""
from shared.agent_tools import AgentTools


def test_get_eligibility_checklist_returns_questions():
    phone = "+919876502001"
    AgentTools(phone, channel="voice").complete_donor_registration(
        name="Elig Donor", age=30, weight=70, blood_group="O+", area="Madhapur")
    checklist = AgentTools(phone, channel="voice").get_eligibility_checklist()
    assert checklist["totalCount"] == 5
    assert len(checklist["checks"]) == 5
    assert checklist["readyToEvaluate"] is False
    assert "diabetes_insulin" in checklist["missingAnswers"]


def test_save_partial_then_check_incomplete():
    phone = "+919876502002"
    tools = AgentTools(phone, channel="voice")
    tools.complete_donor_registration(
        name="Partial Elig", age=30, weight=70, blood_group="A+", area="Gachibowli")
    tools.save_eligibility_answers(tattoo_6mo=False, fever_or_antibiotics_2wk=False)
    result = tools.check_eligibility()
    assert result.get("reason") == "incomplete"
    assert "diabetes_insulin" in result.get("missing", [])


def test_full_eligibility_flow_eligible():
    phone = "+919876502003"
    tools = AgentTools(phone, channel="voice")
    tools.complete_donor_registration(
        name="Full Elig", age=30, weight=70, blood_group="B+", area="Secunderabad")
    tools.save_eligibility_answers(
        diabetes_insulin=False,
        tattoo_6mo=False,
        fever_or_antibiotics_2wk=False,
        pregnant_or_breastfeeding=False,
        malaria_travel_3mo=False,
    )
    result = tools.check_eligibility()
    assert result.get("eligible") is True


def test_checklist_in_get_state_during_outreach():
    phone = "+919876502004"
    tools = AgentTools(phone, channel="voice")
    tools.complete_donor_registration(
        name="Outreach Elig", age=30, weight=70, blood_group="O+", area="Madhapur")
    from shared import vapi_context
    vapi_context.ensure_outreach_primed(phone, "req-elig-1")
    state = tools.get_state()
    assert state["awaitingOutreachReply"] is True
    assert state["eligibilityChecklist"]["totalCount"] == 5
