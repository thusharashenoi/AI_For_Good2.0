"""Multilingual message catalogue for RaktSetu (English / Hindi / Telugu).

Usage:
    from shared import i18n
    i18n.t("WELCOME_NEW", "hi")
    i18n.t("REGISTRATION_COMPLETE", lang, name="Rahul", bloodGroup="O+", area="Madhapur")

Language detection:
    i18n.detect_language(text)          -> "en" | "hi" | "te" from script
    i18n.detect_switch_command(text)    -> "en" | "hi" | "te" | None
"""
from __future__ import annotations

import re
from typing import Dict, Optional

from .branding import BOT_NAME

Lang = str  # "en" | "hi" | "te"
SUPPORTED = ("en", "hi", "te")
DEFAULT_LANG = "en"

# Twilio <Say> language + voice per supported language (FLOW 6).
VOICE_CONFIG: Dict[Lang, Dict[str, str]] = {
    "en": {"language": "en-IN", "voice": "Polly.Aditi"},
    "hi": {"language": "hi-IN", "voice": "Polly.Aditi"},
    "te": {"language": "te-IN", "voice": "Polly.Raveena"},
}

# ---------------------------------------------------------------------------
# Message catalogue. Each key maps to {en, hi, te}. Templates use str.format
# placeholders ({name}, {bloodGroup}, ...).
# ---------------------------------------------------------------------------
MESSAGES: Dict[str, Dict[Lang, str]] = {
    "WELCOME_NEW": {
        "en": (f"Hello! I'm {BOT_NAME} from Blood Warriors 🩸 I'm here to help "
               "with blood donation and requests. Are you a (1) Blood Donor or "
               "(2) Patient/Guardian needing blood?"),
        "hi": (f"नमस्ते! मैं Blood Warriors से {BOT_NAME} हूँ 🩸 रक्तदान "
               "में मदद के लिए यहाँ हूँ। आप (1) रक्तदाता हैं या (2) रक्त की जरूरत है?"),
        "te": (f"నమస్కారం! నేను Blood Warriors నుండి {BOT_NAME} 🩸 రక్తదానంలో సహాయం "
               "చేయడానికి ఇక్కడ ఉన్నాను. మీరు (1) రక్తదాత లేదా (2) రక్తం అవసరమా?"),
    },
    "DONOR_REGISTRATION_START": {
        "en": "Great! Let's get you registered as a Blood Warrior 💪",
        "hi": "बढ़िया! चलिए आपको Blood Warrior के रूप में रजिस्टर करते हैं 💪",
        "te": "అద్భుతం! మిమ్మల్ని Blood Warrior గా నమోదు చేద్దాం 💪",
    },
    "ASK_NAME": {
        "en": "What is your full name?",
        "hi": "आपका पूरा नाम क्या है?",
        "te": "మీ పూర్తి పేరు ఏమిటి?",
    },
    "ASK_AGE": {
        "en": "How old are you?",
        "hi": "आपकी उम्र कितनी है?",
        "te": "మీ వయస్సు ఎంత?",
    },
    "ASK_WEIGHT": {
        "en": "What is your approximate weight in kg?",
        "hi": "आपका लगभग वजन कितना है (किलोग्राम में)?",
        "te": "మీ సుమారు బరువు ఎంత (కిలోలలో)?",
    },
    "ASK_BLOOD_GROUP": {
        "en": "What is your blood group? (e.g. O+, A-, B+, AB+)",
        "hi": "आपका ब्लड ग्रुप क्या है? (जैसे O+, A-, B+, AB+)",
        "te": "మీ బ్లడ్ గ్రూప్ ఏమిటి? (ఉదా. O+, A-, B+, AB+)",
    },
    "ASK_AREA": {
        "en": "Which area/locality are you in? (e.g. Banjara Hills, Madhapur, Secunderabad)",
        "hi": "आप किस इलाके में रहते हैं? (जैसे बंजारा हिल्स, माधापुर, सिकंदराबाद)",
        "te": "మీరు ఏ ప్రాంతంలో ఉన్నారు? (ఉదా. బంజారా హిల్స్, మాధాపూర్, సికింద్రాబాద్)",
    },
    "ASK_LAST_DONATION": {
        "en": "Have you donated blood before?",
        "hi": "क्या आपने पहले रक्तदान किया है?",
        "te": "మీరు ఇంతకుముందు రక్తదానం చేశారా?",
    },
    "ASK_LAST_DONATION_DATE": {
        "en": "When was your last donation? (approximate month and year is fine, e.g. March 2025)",
        "hi": "आपका आखिरी रक्तदान कब था? (लगभग महीना और साल बताएं, जैसे मार्च 2025)",
        "te": "మీ చివరి రక్తదానం ఎప్పుడు? (సుమారు నెల, సంవత్సరం చాలు, ఉదా. మార్చి 2025)",
    },
    "BLOOD_GROUP_UNKNOWN": {
        "en": ("No problem! You can find your blood group from any lab test. We'll complete "
               "your registration once you know. I'll follow up with you in 2 days 😊"),
        "hi": ("कोई बात नहीं! आप किसी भी लैब टेस्ट से अपना ब्लड ग्रुप जान सकते हैं। पता चलने पर हम "
               "रजिस्ट्रेशन पूरा कर देंगे। मैं 2 दिन में फिर संपर्क करूँगा 😊"),
        "te": ("పర్వాలేదు! ఏదైనా ల్యాబ్ టెస్ట్‌లో మీ బ్లడ్ గ్రూప్ తెలుసుకోవచ్చు. తెలిసాక మీ నమోదు "
               "పూర్తి చేస్తాం. 2 రోజుల్లో మళ్లీ సంప్రదిస్తాను 😊"),
    },
    "UNDERAGE": {
        "en": ("Thank you for your enthusiasm! You need to be at least 18 to donate blood. "
               "We'll keep your number and reach out when you're eligible 🙏"),
        "hi": ("आपके उत्साह के लिए धन्यवाद! रक्तदान के लिए कम से कम 18 साल का होना जरूरी है। हम आपका "
               "नंबर रखेंगे और योग्य होने पर संपर्क करेंगे 🙏"),
        "te": ("మీ ఉత్సాహానికి ధన్యవాదాలు! రక్తదానం చేయడానికి కనీసం 18 సంవత్సరాలు ఉండాలి. మీ నంబర్ "
               "ఉంచుకుని, అర్హత వచ్చాక సంప్రదిస్తాం 🙏"),
    },
    "OVERAGE": {
        "en": ("Thank you! Unfortunately blood donation guidelines require donors to be under 65. "
               "You can still support us by spreading awareness 🙏"),
        "hi": ("धन्यवाद! दुर्भाग्यवश रक्तदान दिशानिर्देशों के अनुसार दाता की उम्र 65 से कम होनी चाहिए। "
               "आप जागरूकता फैलाकर हमारा साथ दे सकते हैं 🙏"),
        "te": ("ధన్యవాదాలు! దురదృష్టవశాత్తు రక్తదాతలు 65 సంవత్సరాల లోపు ఉండాలి. అవగాహన పెంచడం "
               "ద్వారా మీరు మాకు సహాయపడవచ్చు 🙏"),
    },
    "UNDERWEIGHT": {
        "en": ("Blood donation requires a minimum weight of 45kg for safety. We'll note your "
               "details and reach out once you meet the criteria 🙏"),
        "hi": ("सुरक्षा के लिए रक्तदान हेतु न्यूनतम 45 किलो वजन जरूरी है। हम आपकी जानकारी रखेंगे और "
               "मानदंड पूरा होने पर संपर्क करेंगे 🙏"),
        "te": ("భద్రత కోసం రక్తదానానికి కనీసం 45 కిలోల బరువు అవసరం. మీ వివరాలు ఉంచుకుని, అర్హత "
               "వచ్చాక సంప్రదిస్తాం 🙏"),
    },
    "COOLDOWN_AFTER_LAST_DONATION": {
        "en": ("Thanks for donating before! Since your last donation was recent, you can donate "
               "again from {cooldownEndsAt}. We'll remind you then 🩸"),
        "hi": ("पहले रक्तदान के लिए धन्यवाद! आपका पिछला रक्तदान हाल ही में हुआ था, इसलिए आप "
               "{cooldownEndsAt} से फिर रक्तदान कर सकते हैं। हम आपको याद दिलाएंगे 🩸"),
        "te": ("ఇంతకుముందు రక్తదానం చేసినందుకు ధన్యవాదాలు! మీ చివరి దానం ఇటీవలే జరిగింది కాబట్టి, "
               "{cooldownEndsAt} నుండి మళ్లీ రక్తదానం చేయవచ్చు. అప్పుడు గుర్తు చేస్తాం 🩸"),
    },
    "REGISTRATION_COMPLETE": {
        "en": ("🎉 You're now a Blood Warrior! Here's your summary:\n\n"
               "Name: {name}\nBlood Group: {bloodGroup}\nArea: {area}\nStatus: {statusLabel}\n\n"
               "You'll receive a WhatsApp message when a patient nearby needs your blood group. "
               "By replying, you agree to be contacted for donation requests.\n\n"
               "Thank you for joining the fight against Thalassemia 🩸"),
        "hi": ("🎉 अब आप एक Blood Warrior हैं! आपका विवरण:\n\n"
               "नाम: {name}\nब्लड ग्रुप: {bloodGroup}\nइलाका: {area}\nस्थिति: {statusLabel}\n\n"
               "जब आपके पास के किसी मरीज़ को आपके ब्लड ग्रुप की जरूरत होगी, आपको WhatsApp संदेश मिलेगा। "
               "जवाब देकर आप दान अनुरोध के लिए संपर्क हेतु सहमति देते हैं।\n\n"
               "थैलेसीमिया के खिलाफ लड़ाई में शामिल होने के लिए धन्यवाद 🩸"),
        "te": ("🎉 ఇప్పుడు మీరు Blood Warrior! మీ సారాంశం:\n\n"
               "పేరు: {name}\nబ్లడ్ గ్రూప్: {bloodGroup}\nప్రాంతం: {area}\nస్థితి: {statusLabel}\n\n"
               "మీ ప్రాంతంలో రోగికి మీ బ్లడ్ గ్రూప్ అవసరమైనప్పుడు WhatsApp సందేశం వస్తుంది. "
               "సమాధానమిచ్చడం ద్వారా దాన అభ్యర్థనల కోసం సంప్రదించడానికి అంగీకరిస్తారు.\n\n"
               "థలసేమియాపై పోరాటంలో చేరినందుకు ధన్యవాదాలు 🩸"),
    },
    "STATUS_ELIGIBLE": {
        "en": "✅ Eligible to donate", "hi": "✅ रक्तदान के योग्य", "te": "✅ రక్తదానానికి అర్హులు",
    },
    "STATUS_COOLDOWN": {
        "en": "⏳ In cooldown", "hi": "⏳ कूलडाउन में", "te": "⏳ కూల్‌డౌన్‌లో",
    },
    # --- Patient flow ---
    "PATIENT_REGISTRATION_START": {
        "en": "I'm sorry you're going through this — we'll help as fast as we can 🙏",
        "hi": "हमें खेद है कि आप इस स्थिति से गुजर रहे हैं — हम जल्द से जल्द मदद करेंगे 🙏",
        "te": "మీరు ఈ పరిస్థితిని ఎదుర్కొంటున్నందుకు చింతిస్తున్నాం — వీలైనంత త్వరగా సహాయం చేస్తాం 🙏",
    },
    "ASK_PATIENT_NAME": {
        "en": "What is the patient's full name?",
        "hi": "मरीज़ का पूरा नाम क्या है?",
        "te": "రోగి పూర్తి పేరు ఏమిటి?",
    },
    "ASK_PATIENT_AGE": {
        "en": "How old is the patient?",
        "hi": "मरीज़ की उम्र कितनी है?",
        "te": "రోగి వయస్సు ఎంత?",
    },
    "ASK_BLOOD_GROUP_NEEDED": {
        "en": "Which blood group is needed?",
        "hi": "किस ब्लड ग्रुप की जरूरत है?",
        "te": "ఏ బ్లడ్ గ్రూప్ అవసరం?",
    },
    "ASK_UNITS": {
        "en": "How many units of blood are needed?",
        "hi": "कितनी यूनिट रक्त की जरूरत है?",
        "te": "ఎన్ని యూనిట్ల రక్తం అవసరం?",
    },
    "ASK_HOSPITAL": {
        "en": "Which hospital is the patient admitted to or will visit for transfusion?",
        "hi": "मरीज़ किस अस्पताल में भर्ती है या ट्रांसफ्यूजन के लिए जाएगा?",
        "te": "రోగి ఏ ఆసుపత్రిలో చేరారు లేదా ట్రాన్స్‌ఫ్యూజన్ కోసం వెళ్తారు?",
    },
    "ASK_REQUIRED_BY": {
        "en": "When is the blood needed by? (e.g. tomorrow, 15 June, within 3 days)",
        "hi": "रक्त कब तक चाहिए? (जैसे कल, 15 जून, 3 दिन के भीतर)",
        "te": "రక్తం ఎప్పటిలోగా కావాలి? (ఉదా. రేపు, జూన్ 15, 3 రోజుల్లో)",
    },
    "REQUEST_RAISED": {
        "en": ("✅ Blood request raised successfully!\n\n"
               "Patient: {patientName}\nBlood Group: {bloodGroup}\nHospital: {hospital}\n"
               "Required By: {requiredBy}\n\n"
               "We are reaching out to compatible donors in your area right now. You'll receive "
               "a WhatsApp update as soon as a donor confirms 🙏\n\nFor urgent help, call: {helpline}"),
        "hi": ("✅ रक्त अनुरोध सफलतापूर्वक दर्ज हुआ!\n\n"
               "मरीज़: {patientName}\nब्लड ग्रुप: {bloodGroup}\nअस्पताल: {hospital}\n"
               "कब तक: {requiredBy}\n\n"
               "हम अभी आपके इलाके के उपयुक्त दाताओं से संपर्क कर रहे हैं। दाता के पुष्टि करते ही आपको "
               "WhatsApp अपडेट मिलेगा 🙏\n\nतत्काल मदद के लिए कॉल करें: {helpline}"),
        "te": ("✅ రక్త అభ్యర్థన విజయవంతంగా నమోదైంది!\n\n"
               "రోగి: {patientName}\nబ్లడ్ గ్రూప్: {bloodGroup}\nఆసుపత్రి: {hospital}\n"
               "ఎప్పటిలోగా: {requiredBy}\n\n"
               "మీ ప్రాంతంలోని అనుకూల దాతలను ఇప్పుడే సంప్రదిస్తున్నాం. దాత నిర్ధారించగానే మీకు WhatsApp "
               "అప్‌డేట్ వస్తుంది 🙏\n\nతక్షణ సహాయానికి కాల్ చేయండి: {helpline}"),
    },
    "DONOR_CONFIRMED_TO_PATIENT": {
        "en": ("🎉 Great news! A donor has confirmed for {patientName}.\n\n"
               "Donor: {donorName} (Blood Group: {bloodGroup})\n"
               "Appointment: {appointmentDate} at {appointmentTime}\nHospital: {hospital}\n\n"
               "The donor will arrive at the blood bank. Please ensure the patient is ready.\n\n"
               "If you need to reschedule, reply RESCHEDULE.\nThank you for trusting Blood Warriors 🩸"),
        "hi": ("🎉 खुशखबरी! {patientName} के लिए एक दाता ने पुष्टि की है।\n\n"
               "दाता: {donorName} (ब्लड ग्रुप: {bloodGroup})\n"
               "अपॉइंटमेंट: {appointmentDate} को {appointmentTime} बजे\nअस्पताल: {hospital}\n\n"
               "दाता ब्लड बैंक पहुंचेंगे। कृपया मरीज़ को तैयार रखें।\n\n"
               "रीशेड्यूल के लिए RESCHEDULE लिखें।\nBlood Warriors पर भरोसे के लिए धन्यवाद 🩸"),
        "te": ("🎉 శుభవార్త! {patientName} కోసం ఒక దాత నిర్ధారించారు.\n\n"
               "దాత: {donorName} (బ్లడ్ గ్రూప్: {bloodGroup})\n"
               "అపాయింట్‌మెంట్: {appointmentDate} {appointmentTime}కి\nఆసుపత్రి: {hospital}\n\n"
               "దాత బ్లడ్ బ్యాంక్‌కు వస్తారు. దయచేసి రోగిని సిద్ధంగా ఉంచండి.\n\n"
               "రీషెడ్యూల్ కోసం RESCHEDULE అని పంపండి.\nBlood Warriors ను నమ్మినందుకు ధన్యవాదాలు 🩸"),
    },
    # --- Eligibility quick-check (FLOW 5) ---
    "ELIGIBILITY_CHECK_START": {
        "en": "Great — 5 quick safety checks (tap ✅ YES or ❌ NO for each):",
        "hi": "बढ़िया — 5 त्वरित सुरक्षा जांच (हर एक के लिए ✅ YES या ❌ NO):",
        "te": "బాగుంది — 5 త్వరిత భద్రతా ప్రశ్నలు (ప్రతి ఒక్కదానికi ✅ YES లేదా ❌ NO):",
    },
    "ELIG_QUESTION_BUTTONS": {
        "en": ("🩸 Quick check {step}/{total}\n{question}\n\n"
               "Tap to reply:\n✅ YES    ·    ❌ NO"),
        "hi": ("🩸 त्वरित जांच {step}/{total}\n{question}\n\n"
               "उत्तर दें:\n✅ YES    ·    ❌ NO"),
        "te": ("🩸 త్వరిత తనిఖీ {step}/{total}\n{question}\n\n"
               "సమాధానం:\n✅ YES    ·    ❌ NO"),
    },
    "BRIDGE_ELIGIBLE_BOOKED": {
        "en": ("You're cleared to donate! ✅\n\n"
               "We've booked you using your Blood Graph profile "
               "({donorName}, {bloodGroup}, {city}).\n\n"
               "Appointment details follow 👇"),
        "hi": ("आप दान के लिए तैयार हैं! ✅\n\n"
               "Blood Graph प्रोफ़ाइल ({donorName}, {bloodGroup}, {city}) से बुक किया।\n\n"
               "अपॉइंटमेंट विवरण 👇"),
        "te": ("మీరు దానం చేయడానికి సిద్ధం! ✅\n\n"
               "Blood Graph ప్రొఫైల్ ({donorName}, {bloodGroup}, {city}) తో బుక్ చేసాం.\n\n"
               "అపాయింట్‌మెంట్ వివరాలు 👇"),
    },
    "ASK_DIABETES": {
        "en": "Do you have diabetes that requires insulin?",
        "hi": "क्या आपको इंसुलिन वाली डायबिटीज है?",
        "te": "మీకు ఇన్సులిన్ అవసరమయ్యే మధుమేహం ఉందా?",
    },
    "ASK_RECENT_TATTOO": {
        "en": "Any tattoo or piercing in the last 6 months?",
        "hi": "पिछले 6 महीनों में कोई टैटू या पियर्सिंग?",
        "te": "గత 6 నెలల్లో పచ్చబొట్టు లేదా పియర్సింగ్ చేశారా?",
    },
    "ASK_RECENT_FEVER": {
        "en": "Fever or antibiotics in the last 2 weeks?",
        "hi": "पिछले 2 हफ्तों में बुखार या एंटीबायोटिक?",
        "te": "గత 2 వారాల్లో జ్వరం లేదా యాంటీబయోటిక్స్?",
    },
    "ASK_PREGNANT": {
        "en": "Are you pregnant or breastfeeding?",
        "hi": "क्या आप गर्भवती हैं या स्तनपान करा रही हैं?",
        "te": "మీరు గర్భవతిగా ఉన్నారా లేదా తల్లిపాలు ఇస్తున్నారా?",
    },
    "ASK_MALARIA_TRAVEL": {
        "en": "Any travel to malaria-affected areas in the last 3 months?",
        "hi": "पिछले 3 महीनों में मलेरिया प्रभावित क्षेत्रों की यात्रा?",
        "te": "గత 3 నెలల్లో మలేరియా ప్రభావిత ప్రాంతాలకు ప్రయాణం చేశారా?",
    },
    "ASK_MEDICATIONS": {
        "en": "Are you currently on any regular medications?",
        "hi": "क्या आप अभी कोई नियमित दवा ले रहे हैं?",
        "te": "మీరు ప్రస్తుతం ఏదైనా క్రమమైన మందులు తీసుకుంటున్నారా?",
    },
    "INELIGIBLE_MESSAGE": {
        "en": ("Thank you for being honest — that's important for patient safety 🙏\n"
               "Based on your answer, you're temporarily not eligible to donate right now.\n"
               "{reason}\nWe'll reach out again on {eligibleDate}. You're still a Blood Warrior! 💪"),
        "hi": ("ईमानदारी के लिए धन्यवाद — यह मरीज़ की सुरक्षा के लिए जरूरी है 🙏\n"
               "आपके उत्तर के अनुसार, आप अभी अस्थायी रूप से रक्तदान के योग्य नहीं हैं।\n"
               "{reason}\nहम {eligibleDate} को फिर संपर्क करेंगे। आप अब भी एक Blood Warrior हैं! 💪"),
        "te": ("నిజాయితీగా చెప్పినందుకు ధన్యవాదాలు — ఇది రోగి భద్రతకు ముఖ్యం 🙏\n"
               "మీ సమాధానం ఆధారంగా, మీరు ప్రస్తుతం తాత్కాలికంగా రక్తదానానికి అర్హులు కాదు.\n"
               "{reason}\n{eligibleDate} నాడు మళ్లీ సంప్రదిస్తాం. మీరు ఇంకా Blood Warrior! 💪"),
    },
    "ELIGIBLE_CONFIRMATION": {
        "en": ("You're all clear! ✅\n\nHere are the appointment details:\n"
               "📍 Hospital: {hospital}\n📅 Date: {appointmentDate}\n⏰ Time: {appointmentTime}\n\n"
               "The donation takes about 30 minutes. Please have a light meal before coming.\n\n"
               "Does this work for you?"),
        "hi": ("आप पूरी तरह तैयार हैं! ✅\n\nअपॉइंटमेंट विवरण:\n"
               "📍 अस्पताल: {hospital}\n📅 तारीख: {appointmentDate}\n⏰ समय: {appointmentTime}\n\n"
               "रक्तदान में लगभग 30 मिनट लगते हैं। आने से पहले हल्का भोजन कर लें।\n\nक्या यह आपके लिए ठीक है?"),
        "te": ("మీరు పూర్తిగా సిద్ధం! ✅\n\nఅపాయింట్‌మెంట్ వివరాలు:\n"
               "📍 ఆసుపత్రి: {hospital}\n📅 తేదీ: {appointmentDate}\n⏰ సమయం: {appointmentTime}\n\n"
               "రక్తదానానికి సుమారు 30 నిమిషాలు పడుతుంది. రాకముందు తేలికపాటి భోజనం చేయండి.\n\nఇది మీకు సరిపోతుందా?"),
    },
    "APPOINTMENT_DETAILS": {
        "en": ("🎉 Appointment Confirmed!\n\n📋 Your Donation Details:\n"
               "Patient: {patientName} (Thalassemia patient)\nHospital: {hospital}\n"
               "Address: {hospitalAddress}\nDate: {appointmentDate}\nTime: {appointmentTime}\n\n"
               "📌 What to bring: Any valid ID\n🍽️ Remember: Eat a light meal before donating\n"
               "💧 Stay hydrated — drink water before coming\n\n"
               "We'll send you a reminder tomorrow. Thank you for saving a life! 🙏🩸\n\n"
               "📅 Add to Calendar: {calendarLink}"),
        "hi": ("🎉 अपॉइंटमेंट कन्फर्म!\n\n📋 आपके रक्तदान का विवरण:\n"
               "मरीज़: {patientName} (थैलेसीमिया मरीज़)\nअस्पताल: {hospital}\n"
               "पता: {hospitalAddress}\nतारीख: {appointmentDate}\nसमय: {appointmentTime}\n\n"
               "📌 साथ लाएं: कोई वैध ID\n🍽️ याद रखें: दान से पहले हल्का भोजन करें\n"
               "💧 हाइड्रेटेड रहें — आने से पहले पानी पिएं\n\n"
               "हम कल आपको रिमाइंडर भेजेंगे। जीवन बचाने के लिए धन्यवाद! 🙏🩸\n\n"
               "📅 कैलेंडर में जोड़ें: {calendarLink}"),
        "te": ("🎉 అపాయింట్‌మెంట్ నిర్ధారించబడింది!\n\n📋 మీ రక్తదాన వివరాలు:\n"
               "రోగి: {patientName} (థలసేమియా రోగి)\nఆసుపత్రి: {hospital}\n"
               "చిరునామా: {hospitalAddress}\nతేదీ: {appointmentDate}\nసమయం: {appointmentTime}\n\n"
               "📌 తీసుకురావాల్సినవి: ఏదైనా చెల్లుబాటు ID\n🍽️ గుర్తుంచుకోండి: దానానికి ముందు తేలికపాటి భోజనం\n"
               "💧 నీరు తాగండి — రాకముందు నీళ్లు తాగండి\n\n"
               "రేపు మీకు రిమైండర్ పంపుతాం. ప్రాణం కాపాడినందుకు ధన్యవాదాలు! 🙏🩸\n\n"
               "📅 క్యాలెండర్‌కు జోడించండి: {calendarLink}"),
    },
    "DIFFERENT_DATE_ASK": {
        "en": "What date works best for you? (The patient needs blood by {requiredBy}, so ideally before then)",
        "hi": "कौन सी तारीख आपके लिए सही है? (मरीज़ को {requiredBy} तक रक्त चाहिए, इसलिए उससे पहले बेहतर है)",
        "te": "మీకు ఏ తేదీ సరిపోతుంది? (రోగికి {requiredBy} లోగా రక్తం కావాలి, కాబట్టి అంతకుముందు మంచిది)",
    },
    # --- Appointment reminders (FLOW 7) ---
    "APPOINTMENT_REMINDER": {
        "en": ("🩸 Reminder: Your blood donation appointment is TOMORROW!\n\n"
               "📍 {hospital}\n⏰ {appointmentTime}\n👤 For patient: {patientName}\n\n"
               "Please remember:\n✅ Have a light meal (avoid heavy/oily food)\n✅ Drink plenty of water\n"
               "✅ Bring any valid ID\n✅ Get 7-8 hours sleep tonight\n\nCan you confirm you'll make it tomorrow?"),
        "hi": ("🩸 रिमाइंडर: आपका रक्तदान अपॉइंटमेंट कल है!\n\n"
               "📍 {hospital}\n⏰ {appointmentTime}\n👤 मरीज़ के लिए: {patientName}\n\n"
               "कृपया याद रखें:\n✅ हल्का भोजन करें (भारी/तला हुआ न खाएं)\n✅ खूब पानी पिएं\n"
               "✅ कोई वैध ID लाएं\n✅ आज रात 7-8 घंटे सोएं\n\nक्या आप कल आने की पुष्टि कर सकते हैं?"),
        "te": ("🩸 రిమైండర్: మీ రక్తదాన అపాయింట్‌మెంట్ రేపు!\n\n"
               "📍 {hospital}\n⏰ {appointmentTime}\n👤 రోగి కోసం: {patientName}\n\n"
               "దయచేసి గుర్తుంచుకోండి:\n✅ తేలికపాటి భోజనం (బరువు/నూనె ఆహారం వద్దు)\n✅ పుష్కలంగా నీరు తాగండి\n"
               "✅ చెల్లుబాటు ID తీసుకురండి\n✅ ఈ రాత్రి 7-8 గంటలు నిద్రపోండి\n\nరేపు వస్తారని నిర్ధారించగలరా?"),
    },
    "DAY_BEFORE_CONFIRMED": {
        "en": "Perfect! See you tomorrow. You're a lifesaver — literally! 🦸 🩸",
        "hi": "बढ़िया! कल मिलते हैं। आप सचमुच एक जीवनरक्षक हैं! 🦸 🩸",
        "te": "అద్భుతం! రేపు కలుద్దాం. మీరు నిజంగా ప్రాణదాత! 🦸 🩸",
    },
    "CANCEL_REASON_ASK": {
        "en": ("We understand. Can you tell us why so we can help?\n"
               "(1) Feeling unwell  (2) Work/emergency  (3) Changed my mind  (4) Other"),
        "hi": ("हम समझते हैं। क्या आप कारण बता सकते हैं ताकि हम मदद कर सकें?\n"
               "(1) तबीयत ठीक नहीं  (2) काम/आपातकाल  (3) मन बदल गया  (4) अन्य"),
        "te": ("మాకు అర్థమైంది. మేము సహాయం చేయడానికి కారణం చెప్పగలరా?\n"
               "(1) ఆరోగ్యం బాగాలేదు  (2) పని/అత్యవసరం  (3) మనసు మారింది  (4) ఇతర"),
    },
    "CANCEL_REPLACEMENT_PATIENT": {
        "en": ("One of your donors had to cancel. We are finding a replacement right now — "
               "you will hear from us within the hour 🙏"),
        "hi": ("आपके एक दाता को रद्द करना पड़ा। हम अभी प्रतिस्थापन ढूंढ रहे हैं — एक घंटे के भीतर आपको "
               "जानकारी मिलेगी 🙏"),
        "te": ("మీ దాతలలో ఒకరు రద్దు చేయవలసి వచ్చింది. మేము ఇప్పుడే ప్రత్యామ్నాయాన్ని వెతుకుతున్నాం — "
               "ఒక గంటలో మీకు తెలియజేస్తాం 🙏"),
    },
    "THREE_HOUR_REMINDER": {
        "en": ("⏰ Your donation is in 3 hours!\n\n📍 {hospital}\n🗺️ {googleMapsLink}\n⏰ {appointmentTime}\n\n"
               "The patient and their family are counting on you 🙏\nSee you soon! 🩸"),
        "hi": ("⏰ आपका रक्तदान 3 घंटे में है!\n\n📍 {hospital}\n🗺️ {googleMapsLink}\n⏰ {appointmentTime}\n\n"
               "मरीज़ और उनका परिवार आप पर भरोसा कर रहे हैं 🙏\nजल्द मिलते हैं! 🩸"),
        "te": ("⏰ మీ రక్తదానం 3 గంటల్లో!\n\n📍 {hospital}\n🗺️ {googleMapsLink}\n⏰ {appointmentTime}\n\n"
               "రోగి, వారి కుటుంబం మీపై ఆధారపడ్డారు 🙏\nత్వరలో కలుద్దాం! 🩸"),
    },
    # --- Follow-up + outreach replies ---
    "FOLLOW_UP_INCOMPLETE_REGISTRATION": {
        "en": ("Hi {name}! 👋 You started registering as a Blood Warrior 2 days ago but didn't "
               "complete it. Thalassemia patients need regular donors — your registration could "
               "save a life! Want to continue? Just reply YES 🩸"),
        "hi": ("नमस्ते {name}! 👋 आपने 2 दिन पहले Blood Warrior के रूप में रजिस्टर करना शुरू किया था "
               "पर पूरा नहीं किया। थैलेसीमिया मरीज़ों को नियमित दाताओं की जरूरत है — आपका रजिस्ट्रेशन "
               "एक जान बचा सकता है! जारी रखना चाहते हैं? बस YES लिखें 🩸"),
        "te": ("నమస్కారం {name}! 👋 మీరు 2 రోజుల క్రితం Blood Warrior గా నమోదు మొదలుపెట్టారు కానీ "
               "పూర్తి చేయలేదు. థలసేమియా రోగులకు క్రమమైన దాతలు అవసరం — మీ నమోదు ఒక ప్రాణాన్ని "
               "కాపాడవచ్చు! కొనసాగించాలా? YES అని పంపండి 🩸"),
    },
    "FOLLOW_UP_FINAL": {
        "en": ("Hi {name}, this is our last reminder 🙏 If you'd like to become a Blood Warrior, "
               "just reply YES anytime. Thank you for considering it 🩸"),
        "hi": ("नमस्ते {name}, यह हमारा आखिरी रिमाइंडर है 🙏 अगर आप Blood Warrior बनना चाहें तो कभी भी "
               "YES लिखें। विचार करने के लिए धन्यवाद 🩸"),
        "te": ("నమస్కారం {name}, ఇది మా చివరి రిమైండర్ 🙏 మీరు Blood Warrior కావాలనుకుంటే ఎప్పుడైనా "
               "YES అని పంపండి. ఆలోచించినందుకు ధన్యవాదాలు 🩸"),
    },
    "COOLDOWN_ELIGIBLE_AGAIN": {
        "en": ("Good news {name}! 🩸 Your 90-day waiting period is over and you're eligible to donate "
               "again. Thalassemia patients need heroes like you. Reply DONATE if you're ready to help 💪"),
        "hi": ("खुशखबरी {name}! 🩸 आपकी 90 दिन की प्रतीक्षा अवधि समाप्त हो गई है और आप फिर रक्तदान के "
               "योग्य हैं। थैलेसीमिया मरीज़ों को आप जैसे नायकों की जरूरत है। तैयार हों तो DONATE लिखें 💪"),
        "te": ("శుభవార్త {name}! 🩸 మీ 90 రోజుల నిరీక్షణ ముగిసింది, మీరు మళ్లీ రక్తదానానికి అర్హులు. "
               "థలసేమియా రోగులకు మీలాంటి హీరోలు అవసరం. సిద్ధంగా ఉంటే DONATE అని పంపండి 💪"),
    },
    "OUTREACH_DECLINED": {
        "en": ("Thank you for letting us know 🙏 We'll reach out next time. If your situation "
               "changes, just reply DONATE."),
        "hi": ("बताने के लिए धन्यवाद 🙏 हम अगली बार संपर्क करेंगे। अगर आपकी स्थिति बदले तो DONATE लिखें।"),
        "te": ("తెలియజేసినందుకు ధన్యవాదాలు 🙏 తదుపరిసారి సంప్రదిస్తాం. మీ పరిస్థితి మారితే DONATE అని పంపండి."),
    },
    "OUTREACH_LATER_ASK": {
        "en": "Of course! When would work for you? (e.g. this weekend, next week)",
        "hi": "ज़रूर! आपके लिए कब सही रहेगा? (जैसे इस वीकेंड, अगले हफ्ते)",
        "te": "తప్పకుండా! మీకు ఎప్పుడు అనుకూలం? (ఉదా. ఈ వారాంతం, వచ్చే వారం)",
    },
    "OUTREACH_SNOOZED": {
        "en": "Got it — we'll reach out again around {date}. Thank you, Blood Warrior 🩸",
        "hi": "ठीक है — हम {date} के आसपास फिर संपर्क करेंगे। धन्यवाद, Blood Warrior 🩸",
        "te": "సరే — {date} సమయంలో మళ్లీ సంప్రదిస్తాం. ధన్యవాదాలు, Blood Warrior 🩸",
    },
    "NO_DONORS_FOUND_PATIENT": {
        "en": ("We are still searching for a matching donor for {patientName}. Our team has been "
               "alerted and will personally help. For urgent needs please call {helpline} 🙏"),
        "hi": ("हम अभी भी {patientName} के लिए उपयुक्त दाता खोज रहे हैं। हमारी टीम को सूचित कर दिया गया "
               "है और वे व्यक्तिगत रूप से मदद करेंगे। तत्काल जरूरत के लिए {helpline} पर कॉल करें 🙏"),
        "te": ("{patientName} కోసం అనుకూల దాత కోసం ఇంకా వెతుకుతున్నాం. మా బృందానికి తెలియజేశాం, వారు "
               "వ్యక్తిగతంగా సహాయం చేస్తారు. తక్షణ అవసరాలకు {helpline} కు కాల్ చేయండి 🙏"),
    },
    "LANGUAGE_SWITCHED": {
        "en": "Sure, let's continue in English 🙂",
        "hi": "ठीक है, हिंदी में जारी रखते हैं 🙂",
        "te": "సరే, తెలుగులో కొనసాగిద్దాం 🙂",
    },
    "DATA_DELETED": {
        "en": ("Your data has been deleted from RaktSetu as requested. We're sorry to see you go. "
               "You can rejoin anytime by messaging us 🙏"),
        "hi": ("आपके अनुरोध पर आपका डेटा RaktSetu से हटा दिया गया है। हमें खेद है। आप कभी भी संदेश "
               "भेजकर दोबारा जुड़ सकते हैं 🙏"),
        "te": ("మీ అభ్యర్థన మేరకు మీ డేటా RaktSetu నుండి తొలగించబడింది. మిమ్మల్ని కోల్పోతున్నందుకు "
               "చింతిస్తున్నాం. ఎప్పుడైనా సందేశం పంపి తిరిగి చేరవచ్చు 🙏"),
    },
    "OUT_OF_SCOPE": {
        "en": ("I'm here specifically to help with blood donation coordination. For medical advice, "
               "please consult your doctor. Is there anything I can help with for blood donation? 🩸"),
        "hi": ("मैं विशेष रूप से रक्तदान समन्वय में मदद के लिए हूँ। चिकित्सकीय सलाह के लिए कृपया अपने "
               "डॉक्टर से परामर्श करें। क्या रक्तदान में कोई मदद कर सकता हूँ? 🩸"),
        "te": ("నేను ప్రత్యేకంగా రక్తదాన సమన్వయంలో సహాయం చేయడానికి ఉన్నాను. వైద్య సలహా కోసం దయచేసి మీ "
               "వైద్యుడిని సంప్రదించండి. రక్తదానంలో ఏదైనా సహాయం చేయగలనా? 🩸"),
    },
    "GENERIC_FALLBACK": {
        "en": "Sorry, I didn't quite catch that. Could you please rephrase? 🙏",
        "hi": "माफ़ कीजिए, मैं समझ नहीं पाया। क्या आप दोबारा बता सकते हैं? 🙏",
        "te": "క్షమించండి, నాకు అర్థం కాలేదు. దయచేసి మళ్లీ చెప్పగలరా? 🙏",
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def t(key: str, lang: Lang = DEFAULT_LANG, **kwargs) -> str:
    lang = lang if lang in SUPPORTED else DEFAULT_LANG
    entry = MESSAGES.get(key)
    if not entry:
        return key
    template = entry.get(lang) or entry.get(DEFAULT_LANG, key)
    try:
        return template.format(**kwargs) if kwargs else template
    except (KeyError, IndexError):
        return template


# Devanagari (Hindi) and Telugu Unicode block ranges.
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_TELUGU = re.compile(r"[\u0C00-\u0C7F]")


def detect_language(text: str) -> Lang:
    """Detect language from script content of the first message."""
    if not text:
        return DEFAULT_LANG
    if _TELUGU.search(text):
        return "te"
    if _DEVANAGARI.search(text):
        return "hi"
    return "en"


_SWITCH_PATTERNS = {
    "en": [r"\bswitch to english\b", r"\benglish\b", r"\bin english\b", r"\bangrezi\b"],
    "hi": [r"hindi", r"हिंदी", r"\bhindi mein\b", r"हिंदी में"],
    "te": [r"telugu", r"తెలుగు", r"telugu lo", r"తెలుగులో"],
}


def detect_switch_command(text: str) -> Optional[Lang]:
    """Return a language code if the user explicitly asked to switch, else None."""
    if not text:
        return None
    low = text.lower()
    for lang, patterns in _SWITCH_PATTERNS.items():
        for p in patterns:
            if re.search(p, low):
                return lang
    # native-script tokens
    if "తెలుగు" in text:
        return "te"
    if "हिंदी" in text:
        return "hi"
    return None
