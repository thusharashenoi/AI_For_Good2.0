"""Natural conversation — bundled / out-of-order answers."""
from shared.agent_tools import AgentTools


def test_registration_from_saved_context():
    phone = "+919876502001"
    tools = AgentTools(phone, channel="voice")
    tools.set_state(user_type="donor", context_updates={
        "name": "Rahul Kumar", "age": 28, "weight": 70,
        "blood_group": "O+", "area": "Madhapur", "donated_before": False,
    })
    result = tools.complete_donor_registration()
    assert result["ok"] is True


def test_registration_missing_fields_returns_hint():
    phone = "+919876502002"
    tools = AgentTools(phone, channel="voice")
    tools.set_state(context_updates={"name": "Partial Person", "age": 30})
    result = tools.complete_donor_registration()
    assert result["ok"] is False
    assert result["reason"] == "missing_fields"
    assert "weight" in result["missing"]
    assert "blood_group" in result["missing"]


def test_set_state_reports_missing():
    phone = "+919876502003"
    tools = AgentTools(phone, channel="voice")
    out = tools.set_state(context_updates={"name": "Anjali", "blood_group": "B+"})
    assert "name" not in out["missingForDonorRegistration"]
    assert "blood_group" not in out["missingForDonorRegistration"]
    assert "age" in out["missingForDonorRegistration"]


def test_patient_request_from_bundled_context():
    phone = "+919876502004"
    tools = AgentTools(phone, channel="whatsapp")
    tools.set_state(user_type="patient", context_updates={
        "patient_name": "Baby Rao", "patient_age": 6, "blood_group": "B+",
        "units": 2, "hospital": "Rainbow Hospital", "required_by": "tomorrow",
    })
    result = tools.raise_blood_request()
    assert result["ok"] is True
    assert result["bloodGroup"] == "B+"
