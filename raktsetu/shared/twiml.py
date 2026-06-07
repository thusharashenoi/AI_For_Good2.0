"""TwiML builders for RaktSetu voice flows (FLOW 6).

Pure string builders — no Twilio SDK needed — so they're unit-testable and can
be returned directly from the voice webhook Lambdas.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from . import i18n

XML_HEADER = '<?xml version="1.0" encoding="UTF-8"?>'


def _say(text: str, lang: str) -> str:
    cfg = i18n.VOICE_CONFIG.get(lang, i18n.VOICE_CONFIG["en"])
    return f'<Say voice="{cfg["voice"]}" language="{cfg["language"]}">{escape(text)}</Say>'


def outreach_call(blood_group: str, donor_area: str, lang: str,
                  keypress_action: str) -> str:
    """The escalation IVR offered when a donor doesn't reply on WhatsApp."""
    intro = {
        "en": (f"Hello, this is Tara from Blood Warriors. A Thalassemia patient urgently "
               f"needs {blood_group} blood near {donor_area}. You donated before and made a real "
               f"difference. Press 1 if you can donate, Press 2 if you cannot donate right now."),
        "hi": (f"नमस्ते, यह Blood Warriors की ओर से Tara है। {donor_area} के पास एक थैलेसीमिया "
               f"मरीज़ को तुरंत {blood_group} रक्त चाहिए। आप पहले रक्तदान कर चुके हैं। दान कर सकते हैं "
               f"तो 1 दबाएं, अभी नहीं कर सकते तो 2 दबाएं।"),
        "te": (f"నమస్కారం, ఇది Blood Warriors నుండి Tara. {donor_area} దగ్గర ఒక థలసేమియా రోగికి "
               f"తక్షణమే {blood_group} రక్తం అవసరం. మీరు గతంలో రక్తదానం చేశారు. దానం చేయగలిగితే 1 నొక్కండి, "
               f"ఇప్పుడు చేయలేకపోతే 2 నొక్కండి."),
    }
    timeout_msg = {
        "en": "We did not receive your response. We will try again later. Thank you for being a Blood Warrior.",
        "hi": "हमें आपका उत्तर नहीं मिला। हम बाद में फिर कोशिश करेंगे। Blood Warrior होने के लिए धन्यवाद।",
        "te": "మీ సమాధానం అందలేదు. తర్వాత మళ్లీ ప్రయత్నిస్తాం. Blood Warrior గా ఉన్నందుకు ధన్యవాదాలు.",
    }
    lang = lang if lang in i18n.SUPPORTED else "en"
    return (f"{XML_HEADER}<Response>"
            f"{_say(intro[lang], lang)}"
            f'<Gather numDigits="1" action="{escape(keypress_action)}" method="POST" timeout="10"></Gather>'
            f"{_say(timeout_msg[lang], lang)}"
            f"</Response>")


def keypress_yes(lang: str) -> str:
    msg = {
        "en": "Thank you! We will send you the appointment details on WhatsApp right now.",
        "hi": "धन्यवाद! हम अभी आपको अपॉइंटमेंट विवरण WhatsApp पर भेजेंगे।",
        "te": "ధన్యవాదాలు! మేము ఇప్పుడే మీకు అపాయింట్‌మెంట్ వివరాలను WhatsApp లో పంపుతాం.",
    }
    lang = lang if lang in i18n.SUPPORTED else "en"
    return f"{XML_HEADER}<Response>{_say(msg[lang], lang)}<Hangup/></Response>"


def keypress_no(lang: str) -> str:
    msg = {
        "en": "Thank you for letting us know. We will reach out next time. Take care and God bless.",
        "hi": "बताने के लिए धन्यवाद। हम अगली बार संपर्क करेंगे। अपना ख्याल रखें।",
        "te": "తెలియజేసినందుకు ధన్యవాదాలు. తదుపరిసారి సంప్రదిస్తాం. జాగ్రత్తగా ఉండండి.",
    }
    lang = lang if lang in i18n.SUPPORTED else "en"
    return f"{XML_HEADER}<Response>{_say(msg[lang], lang)}<Hangup/></Response>"


def inbound_welcome(lang: str, gather_action: str) -> str:
    """Inbound registration call: greet + gather speech."""
    from shared.voice_speech import build_inbound_greeting
    msg = build_inbound_greeting()
    lang = lang if lang in i18n.SUPPORTED else "en"
    cfg = i18n.VOICE_CONFIG.get(lang, i18n.VOICE_CONFIG["en"])
    return (f"{XML_HEADER}<Response>"
            f"{_say(msg, lang)}"
            f'<Gather input="speech" language="{cfg["language"]}" speechTimeout="auto" '
            f'action="{escape(gather_action)}" method="POST"></Gather>'
            f"{_say(msg, lang)}"
            f"</Response>")


def say_and_gather(prompt: str, lang: str, gather_action: str,
                   input_type: str = "speech") -> str:
    lang = lang if lang in i18n.SUPPORTED else "en"
    cfg = i18n.VOICE_CONFIG.get(lang, i18n.VOICE_CONFIG["en"])
    if input_type == "speech":
        gather = (f'<Gather input="speech" language="{cfg["language"]}" speechTimeout="auto" '
                  f'action="{escape(gather_action)}" method="POST"></Gather>')
    else:
        gather = (f'<Gather numDigits="1" action="{escape(gather_action)}" '
                  f'method="POST" timeout="10"></Gather>')
    return f"{XML_HEADER}<Response>{_say(prompt, lang)}{gather}</Response>"


def say_and_hangup(message: str, lang: str) -> str:
    return f"{XML_HEADER}<Response>{_say(message, lang)}<Hangup/></Response>"


def confirm_number(number: str, lang: str, action: str) -> str:
    msg = {
        "en": f"To confirm your number is {number}, press 1.",
        "hi": f"पुष्टि करने के लिए कि आपका नंबर {number} है, 1 दबाएं।",
        "te": f"మీ నంబర్ {number} అని నిర్ధారించడానికి 1 నొక్కండి.",
    }
    lang = lang if lang in i18n.SUPPORTED else "en"
    return (f"{XML_HEADER}<Response>{_say(msg[lang], lang)}"
            f'<Gather numDigits="1" action="{escape(action)}" method="POST" timeout="10"></Gather>'
            f"</Response>")
