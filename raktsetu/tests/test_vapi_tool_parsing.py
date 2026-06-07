"""Tests for Vapi tool-call payload normalization."""
from lambdas.vapi_tools.handler import _tool_calls


def test_tool_calls_flat_format():
    message = {
        "toolCallList": [{
            "id": "tc1",
            "name": "book_appointment",
            "arguments": {"request_id": "req-1"},
        }],
    }
    calls = _tool_calls(message)
    assert len(calls) == 1
    assert calls[0]["name"] == "book_appointment"
    assert calls[0]["arguments"]["request_id"] == "req-1"


def test_tool_calls_anthropic_function_format():
    message = {
        "toolCallList": [{
            "id": "toolu_01ABC",
            "type": "function",
            "function": {
                "name": "confirm_appointment_slot",
                "arguments": {"time": "2:00 PM", "date": "tomorrow"},
            },
        }],
    }
    calls = _tool_calls(message)
    assert len(calls) == 1
    assert calls[0]["name"] == "confirm_appointment_slot"
    assert calls[0]["arguments"]["time"] == "2:00 PM"


def test_tool_calls_from_tool_with_tool_call_list():
    message = {
        "toolCallList": [{"id": "toolu_02XYZ", "name": None, "arguments": None}],
        "toolWithToolCallList": [{
            "name": "book_appointment",
            "toolCall": {
                "id": "toolu_02XYZ",
                "type": "function",
                "function": {
                    "name": "book_appointment",
                    "parameters": {"request_id": "req-99"},
                },
            },
        }],
    }
    calls = _tool_calls(message)
    assert len(calls) == 1
    assert calls[0]["name"] == "book_appointment"
    assert calls[0]["arguments"]["request_id"] == "req-99"
