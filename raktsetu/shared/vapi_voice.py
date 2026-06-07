"""Vapi voice + transcriber + latency presets for Tara phone calls."""
from __future__ import annotations

from . import config
from .branding import BOT_NAME, ORG_SPOKEN
from .voice_speech import sanitize_for_speech, build_inbound_greeting

VAPI_FIRST_MESSAGE = build_inbound_greeting()

# set_state runs in background so Tara keeps talking while we persist via ngrok.
_ASYNC_TOOLS = frozenset({"set_state"})

_AZURE_VOICE_FALLBACKS = (
    {"provider": "azure", "voiceId": "hi-IN-SwaraNeural"},
    {"provider": "azure", "voiceId": "te-IN-ShrutiNeural"},
)


def build_voice_config() -> dict:
    provider = (config.get("VAPI_VOICE_PROVIDER") or "vapi").strip().lower()
    voice_id = config.get("VAPI_VOICE_ID") or "Naina"

    if provider == "vapi":
        cfg: dict = {"provider": "vapi", "voiceId": voice_id}
        if (config.get("VAPI_VOICE_VERSION") or "2") == "2":
            cfg["version"] = 2
        return cfg

    if provider == "azure":
        cfg = {
            "provider": "azure",
            "voiceId": voice_id or "en-IN-NeerjaNeural",
            "fallbackPlan": {"voices": list(_AZURE_VOICE_FALLBACKS)},
        }
        speed = config.get("VAPI_VOICE_SPEED")
        if speed:
            cfg["speed"] = float(speed)
        return cfg

    if provider in ("11labs", "elevenlabs"):
        return {
            "provider": "11labs",
            "voiceId": voice_id,
            "model": config.get("VAPI_VOICE_MODEL", "eleven_multilingual_v2"),
            "stability": float(config.get("VAPI_VOICE_STABILITY", "0.55") or 0.55),
            "similarityBoost": float(config.get("VAPI_VOICE_SIMILARITY", "0.78") or 0.78),
            "useSpeakerBoost": True,
        }

    return {"provider": provider, "voiceId": voice_id}


def build_transcriber_config() -> dict:
    return {
        "provider": config.get("VAPI_TRANSCRIBER_PROVIDER", "deepgram"),
        "model": config.get("VAPI_TRANSCRIBER_MODEL", "nova-3"),
        "language": config.get("VAPI_TRANSCRIBER_LANGUAGE", "multi"),
    }


def build_speaking_plans() -> dict:
    """Balanced latency — not so aggressive that single-word names cause hangs."""
    wait = float(config.get("VAPI_WAIT_SECONDS", "0.2") or 0.2)
    endpointing = {
        "onPunctuationSeconds": float(config.get("VAPI_ON_PUNCTUATION_SECONDS", "0.15") or 0.15),
        "onNoPunctuationSeconds": float(config.get("VAPI_ON_NO_PUNCTUATION_SECONDS", "0.55") or 0.55),
        "onNumberSeconds": float(config.get("VAPI_ON_NUMBER_SECONDS", "0.3") or 0.3),
    }
    return {
        "startSpeakingPlan": {
            "waitSeconds": wait,
            "transcriptionEndpointingPlan": endpointing,
            "smartEndpointingPlan": {"provider": "vapi"},
        },
        "stopSpeakingPlan": {
            "numWords": 0,
            "voiceSeconds": 0.25,
            "backoffSeconds": 0.6,
        },
    }


def build_model_config(system_prompt: str, tools: list) -> dict:
    provider = (config.get("VAPI_MODEL_PROVIDER") or "anthropic").strip().lower()
    if provider in ("gemini", "google-gemini"):
        provider = "google"
    default_model = (
        "gemini-2.5-flash" if provider == "google" else "claude-haiku-4-5-20251001"
    )
    return {
        "provider": provider,
        "model": config.get("VAPI_MODEL") or default_model,
        "temperature": float(config.get("VAPI_MODEL_TEMPERATURE", "0.3") or 0.3),
        "maxTokens": int(config.get("VAPI_MODEL_MAX_TOKENS", "220") or 220),
        "messages": [{"role": "system", "content": system_prompt}],
        "tools": tools,
    }


def tool_server(tools_url: str) -> dict:
    return {"url": tools_url, "headers": {"ngrok-skip-browser-warning": "true"}}


def build_assistant_payload(server_url: str, name: str, system_prompt: str, tool_schemas: list) -> dict:
    tools_url = server_url.rstrip("/") + "/vapi/tools"
    ts = tool_server(tools_url)
    tools = [{
        "type": "function",
        "async": t["name"] in _ASYNC_TOOLS,
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": {
                "type": "object",
                "properties": t["parameters"].get("properties", {}),
                "required": t["parameters"].get("required", []),
            },
        },
        "server": ts,
    } for t in tool_schemas]
    tools.append({"type": "endCall"})

    return {
        "name": name,
        "firstMessage": VAPI_FIRST_MESSAGE,
        "model": build_model_config(system_prompt, tools),
        "voice": build_voice_config(),
        "transcriber": build_transcriber_config(),
        "server": ts,
        "serverMessages": ["tool-calls", "end-of-call-report", "status-update"],
        "endCallFunctionEnabled": True,
        "endCallMessage": sanitize_for_speech(
            f"Thank you for calling {ORG_SPOKEN}. Namaste."
        ),
        "backgroundDenoisingEnabled": False,
        **build_speaking_plans(),
    }
