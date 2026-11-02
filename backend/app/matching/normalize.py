"""
Text normalisation for matching: case folding, punctuation removal, Hindi/Hinglish synonyms mapped to one
canonical English token, and stop-word removal. Deterministic and dictionary-based; no models.
"""

import re
import unicodedata

# Canonical colour -> spellings seen at help desks (English, Hindi in Devanagari, romanised Hindi).
COLOURS: dict[str, tuple[str, ...]] = {
    "red": ("red", "लाल", "lal", "laal", "maroon", "मैरून"),
    "blue": ("blue", "नीला", "नीली", "neela", "nila", "neeli", "navy"),
    "green": ("green", "हरा", "हरी", "hara", "hari"),
    "yellow": ("yellow", "पीला", "पीली", "peela", "pila", "peeli"),
    "white": ("white", "सफेद", "सफ़ेद", "safed", "safaid"),
    "black": ("black", "काला", "काली", "kala", "kaala", "kali"),
    "orange": ("orange", "saffron", "भगवा", "केसरिया", "bhagwa", "kesariya", "narangi", "नारंगी"),
    "pink": ("pink", "गुलाबी", "gulabi"),
    "brown": ("brown", "भूरा", "भूरी", "bhura", "bhoora"),
    "grey": ("grey", "gray", "स्लेटी", "sleti"),
    "purple": ("purple", "बैंगनी", "baingani", "violet"),
}

GARMENTS: dict[str, tuple[str, ...]] = {
    "kurta": ("kurta", "kurti", "कुर्ता", "कुर्ती"),
    "saree": ("saree", "sari", "साड़ी", "साडी"),
    "dhoti": ("dhoti", "धोती"),
    "shirt": ("shirt", "kameez", "कमीज", "कमीज़", "शर्ट"),
    "trousers": ("trousers", "pant", "pants", "पैंट", "पजामा", "pajama", "pyjama"),
    "shorts": ("shorts", "nikkar", "निक्कर"),
    "frock": ("frock", "फ्रॉक", "dress"),
    "shawl": ("shawl", "शॉल", "dupatta", "दुपट्टा", "chunni", "चुन्नी"),
    "cap": ("cap", "topi", "टोपी", "hat"),
    "sweater": ("sweater", "स्वेटर", "jacket", "जैकेट"),
    "lehenga": ("lehenga", "लहंगा", "ghagra", "घाघरा"),
    "slippers": ("slippers", "chappal", "चप्पल", "sandals"),
    "turban": ("turban", "pagdi", "पगड़ी", "safa", "साफा"),
}

ITEM_TERMS: dict[str, tuple[str, ...]] = {
    "bag": ("bag", "thaila", "थैला", "jhola", "झोला", "backpack", "pitthu", "पिट्ठू"),
    "phone": ("phone", "mobile", "smartphone", "फोन", "मोबाइल"),
    "wallet": ("wallet", "purse", "batua", "बटुआ", "पर्स"),
    "spectacles": ("spectacles", "glasses", "chashma", "चश्मा"),
}

STOP_WORDS = frozenset(
    "a an the and or of with in on at to is was has have near wearing wear wore colour color coloured "
    "colored light dark old new small big large some one his her their he she it this that very "
    "का की के है था थी और में पर से".split()
)

_SYNONYMS: dict[str, str] = {}
for table in (COLOURS, GARMENTS, ITEM_TERMS):
    for canonical, spellings in table.items():
        for spelling in spellings:
            _SYNONYMS[spelling.casefold()] = canonical
COLOUR_NAMES = frozenset(COLOURS)

_TOKEN = re.compile(r"[\w\u0900-\u097F]+", re.UNICODE)


def fold(text: str | None) -> str:
    if not text:
        return ""
    normalised = unicodedata.normalize("NFC", text).casefold()
    return " ".join(_TOKEN.findall(normalised))


def tokens(text: str | None) -> set[str]:
    """Canonical content tokens: synonyms collapsed, stop words and single characters dropped."""
    out = set()
    for raw in fold(text).split():
        token = _SYNONYMS.get(raw, raw)
        if token in STOP_WORDS or len(token) < 2 or token.isdigit():
            continue
        out.add(token)
    return out


def colours(text: str | None) -> set[str]:
    return tokens(text) & COLOUR_NAMES


def same_text(a: str | None, b: str | None) -> bool:
    return bool(fold(a)) and fold(a) == fold(b)
