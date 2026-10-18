"""Deterministic offline content for LLM_PROVIDER=mock (tests + dry runs without an API key).

Real runs use the LLM with the prompts in engine/prompts/. These templates only keep the pipeline
exercisable offline and are generic on purpose (no claims, no numbers, no testimonials).
"""
from __future__ import annotations

import random
from typing import Any

HOOKS: dict[str, dict[str, str]] = {
    "hinglish": {
        "question": "Month-end pe paisa kahan gaya, pata hai?",
        "bold_claim": "{app} ke baad tracking literally automatic hai",
        "relatable_pain": "Salary aayi aur 10 din mein gayab?",
        "curiosity_gap": "Ye trick PG wale dost ne sikhayi",
        "number_list": "3 cheezein jo {app} sambhal leta hai",
        "desi_humor": "Mummy: paise kahan gaye? Main: pata nahi",
        "trend_moment": "Festive sale se pehle ye zaroor dekho",
    },
    "hi": {
        "question": "महीने के आखिर में पैसे कहाँ गए, पता है?",
        "bold_claim": "{app} से हिसाब रखना अब आसान है",
        "relatable_pain": "सैलरी आई और दस दिन में खत्म?",
        "curiosity_gap": "ये तरीका हॉस्टल वाले दोस्त ने बताया",
        "number_list": "तीन काम जो {app} आसान कर देता है",
        "desi_humor": "मम्मी: पैसे कहाँ गए? मैं: पता नहीं",
        "trend_moment": "त्योहार की सेल से पहले ये देख लो",
    },
    "ta": {
        "question": "மாச கடைசியில காசு எங்க போச்சுன்னு தெரியுமா?",
        "bold_claim": "{app} இருந்தா கணக்கு பார்க்குறது ரொம்ப ஈஸி",
        "relatable_pain": "சம்பளம் வந்து பத்து நாள்ல காலியா?",
        "curiosity_gap": "இந்த டிப்ஸ் ஹாஸ்டல் நண்பன் சொன்னது",
        "number_list": "{app} எளிதாக்கும் மூணு விஷயம்",
        "desi_humor": "அம்மா: காசு எங்க? நான்: தெரியலையே",
        "trend_moment": "பண்டிகை சேலுக்கு முன்னாடி இதை பாருங்க",
    },
    "te": {
        "question": "నెల చివర్లో డబ్బు ఎక్కడికి పోయిందో తెలుసా?",
        "bold_claim": "{app} తో లెక్కలు చాలా ఈజీ",
        "relatable_pain": "జీతం వచ్చి పది రోజుల్లో ఖాళీనా?",
        "curiosity_gap": "ఈ ట్రిక్ హాస్టల్ ఫ్రెండ్ చెప్పాడు",
        "number_list": "{app} సులువు చేసే మూడు పనులు",
        "desi_humor": "అమ్మ: డబ్బు ఎక్కడ? నేను: తెలీదు",
        "trend_moment": "పండగ సేల్ ముందు ఇది చూడండి",
    },
    "mr": {
        "question": "महिन्याच्या शेवटी पैसे कुठे गेले, माहितीये?",
        "bold_claim": "{app} मुळे हिशोब ठेवणं सोपं झालं",
        "relatable_pain": "पगार आला आणि दहा दिवसांत संपला?",
        "curiosity_gap": "ही ट्रिक हॉस्टेलच्या मित्राने सांगितली",
        "number_list": "{app} सोपी करणारी तीन कामं",
        "desi_humor": "आई: पैसे कुठे गेले? मी: माहीत नाही",
        "trend_moment": "सणाच्या सेलआधी हे नक्की बघा",
    },
    "bn": {
        "question": "মাসের শেষে টাকা কোথায় গেল, জানো?",
        "bold_claim": "{app} দিয়ে হিসেব রাখা এখন সহজ",
        "relatable_pain": "মাইনে এল আর দশ দিনে শেষ?",
        "curiosity_gap": "এই ট্রিকটা হোস্টেলের বন্ধু বলেছিল",
        "number_list": "{app} সহজ করে দেয় এমন তিনটে কাজ",
        "desi_humor": "মা: টাকা কোথায়? আমি: জানি না",
        "trend_moment": "পুজোর সেলের আগে এটা দেখো",
    },
}

SCENES: dict[str, list[str]] = {
    "hinglish": ["Scene ye hai yaar, har din UPI pe chhota chhota kharcha", "Aur month-end pe sirf tension",
                 "Isliye {app} try karo", "Sab kuch khud category mein aa jaata hai",
                 "Ab tumhe pata rahega paisa kahan ja raha hai"],
    "hi": ["रोज़ UPI पर छोटे छोटे खर्च होते हैं", "और महीने के आखिर में बस टेंशन",
           "इसलिए {app} आज़माइए", "सारे खर्च अपने आप अलग हो जाते हैं", "अब आपको पता रहेगा पैसा कहाँ जा रहा है"],
    "ta": ["தினமும் UPI ல சின்ன சின்ன செலவு", "மாச கடைசியில டென்ஷன் மட்டும் தான்",
           "அதனால {app} ட்ரை பண்ணுங்க", "எல்லா செலவும் தானா பிரிஞ்சிடும்", "இனி காசு எங்க போகுதுன்னு உங்களுக்கு தெரியும்"],
    "te": ["రోజూ UPI లో చిన్న చిన్న ఖర్చులు", "నెల చివర్లో టెన్షన్ మాత్రమే",
           "అందుకే {app} ట్రై చేయండి", "ఖర్చులన్నీ ఆటోమేటిక్ గా విడిపోతాయి", "ఇక డబ్బు ఎక్కడికి వెళ్తుందో మీకు తెలుస్తుంది"],
    "mr": ["रोज UPI वर छोटे छोटे खर्च", "आणि महिन्याच्या शेवटी फक्त टेन्शन",
           "म्हणून {app} वापरून बघा", "सगळे खर्च आपोआप वेगळे होतात", "आता तुम्हाला कळेल पैसा कुठे जातोय"],
    "bn": ["রোজ UPI তে ছোট ছোট খরচ", "আর মাসের শেষে শুধু টেনশন",
           "তাই {app} ট্রাই করো", "সব খরচ নিজে থেকেই ভাগ হয়ে যায়", "এবার জানবে টাকা কোথায় যাচ্ছে"],
}

CTA: dict[str, str] = {
    "hinglish": "Play Store pe free download karo", "hi": "Play Store से फ्री डाउनलोड करें",
    "ta": "Play Store ல இலவசமா டவுன்லோட் பண்ணுங்க", "te": "Play Store లో ఫ్రీగా డౌన్‌లోడ్ చేయండి",
    "mr": "Play Store वरून मोफत डाउनलोड करा", "bn": "Play Store থেকে ফ্রি ডাউনলোড করো",
}

STOCK_QUERIES = ["young indian person using phone", "indian street food stall", "mumbai local train",
                 "college hostel room india", "chai cup close up", "upi payment qr code shop",
                 "indian family living room", "city traffic india evening"]


OPENERS: dict[str, list[str]] = {
    "hinglish": ["", "Sach bataun?", "Suno yaar,", "Real talk:", "Ek baat bolun?", "POV:"],
    "hi": ["", "सच बताऊँ?", "सुनो,", "सीधी बात:", "एक बात बोलूँ?", "सोचो,"],
    "ta": ["", "உண்மைய சொல்லட்டுமா?", "கேளுங்க,", "நேரா சொல்றேன்:", "ஒரு விஷயம்:", "யோசிங்க,"],
    "te": ["", "నిజం చెప్పనా?", "వినండి,", "సూటిగా చెప్తా:", "ఒక విషయం:", "ఆలోచించండి,"],
    "mr": ["", "खरं सांगू?", "ऐका,", "सरळ सांगतो:", "एक गोष्ट:", "विचार करा,"],
    "bn": ["", "সত্যি বলব?", "শোনো,", "সোজা কথা:", "একটা কথা:", "ভাবো,"],
}


def hook(lang: str, hook_type: str, app: str, variant: int = 0) -> str:
    base = HOOKS.get(lang, HOOKS["hinglish"]).get(hook_type, HOOKS["hinglish"]["question"]).format(app=app)
    openers = OPENERS.get(lang, OPENERS["hinglish"])
    opener = openers[variant % len(openers)]
    suffix = ["", " 👀", "!", " 🤯", "…"][(variant // len(openers)) % 5]
    return f"{opener} {base}{suffix}".strip()


def script(lang: str, hook_text: str, hook_type: str, fmt: str, style: str, app: str, seed: int,
           n_screens: int = 4) -> dict[str, Any]:
    rng = random.Random(seed)
    lines = [l.format(app=app) for l in SCENES.get(lang, SCENES["hinglish"])]
    n = rng.randint(5, 6)
    scenes = [{"voiceover": hook_text, "on_screen_text": " ".join(hook_text.split()[:6]),
               "visual": {"type": "stock", "query_en": rng.choice(STOCK_QUERIES), "screenshot_index": 0},
               "est_seconds": 3}]
    for i in range(n - 2):
        vo = lines[i % len(lines)]
        shot = i % 2 == 1
        scenes.append({"voiceover": vo, "on_screen_text": " ".join(vo.split()[:5]),
                       "visual": {"type": "screenshot" if shot else "stock", "query_en": rng.choice(STOCK_QUERIES),
                                  "screenshot_index": i % max(1, n_screens)},
                       "est_seconds": 4})
    cta = CTA.get(lang, CTA["hinglish"])
    scenes.append({"voiceover": cta, "on_screen_text": " ".join(cta.split()[:5]),
                   "visual": {"type": "screenshot", "query_en": "phone app store", "screenshot_index": 0},
                   "est_seconds": 3})
    return {
        "hook": hook_text, "hook_type": hook_type, "format": fmt, "language": lang, "creator_style": style,
        "scenes": scenes, "cta": cta, "cta_type": rng.choice(["download", "try_free"]),
        "post_caption": f"{hook_text} — {app}", "hashtags": ["#MoneyTips", "#UPI", "#IndianCreators", f"#{app}"],
        "predicted_score": rng.randint(45, 85), "score_reason": "mock score",
    }
