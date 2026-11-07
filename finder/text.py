"""Text helpers: HTML stripping and UK location detection."""
import html
import re

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(s, limit=6000):
    if not s:
        return ""
    s = html.unescape(s)  # Greenhouse double-escapes its HTML
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", s)
    s = html.unescape(_TAG.sub(" ", s))
    return _WS.sub(" ", s).strip()[:limit]


UK_WORDS = [
    "united kingdom", "great britain", "england", "scotland", "wales", "northern ireland",
    "london", "birmingham", "manchester", "leeds", "glasgow", "edinburgh", "bristol",
    "liverpool", "sheffield", "newcastle", "nottingham", "leicester", "cardiff", "belfast",
    "reading", "milton keynes", "cambridge", "oxford", "southampton", "bournemouth",
    "brighton", "aberdeen", "york", "coventry", "norwich", "exeter", "plymouth", "derby",
    "bath", "guildford", "st albans", "watford", "chester", "warwick", "solihull",
    "canary wharf", "poole", "ipswich", "stoke", "wolverhampton", "luton", "swindon",
    "gloucester", "cheltenham", "harrogate", "chelmsford", "maidstone", "crawley", "dundee",
    "inverness", "swansea", "knutsford", "northampton", "cramlington", "carlisle", "livingston",
    "halifax", "chester", "sheffield", "peterborough", "telford", "worthing", "basingstoke", "farnborough",
    "leatherhead", "horsham", "redhill", "staines", "slough", "bracknell", "uxbridge", "croydon", "wythenshawe", "preston", "bolton", "hull", "middlesbrough", "sunderland",
]
_UK_RX = re.compile(r"\b(" + "|".join(re.escape(w) for w in UK_WORDS) + r")\b|\buk\b|\bgb\b|\bgbr\b", re.I)
# Places that share a name with a UK one.
_FALSE_UK = re.compile(r"united states|\busa\b|\bu\.s\.|canada|australia|new york|new england|london,? (ontario|on\b|ky|kentucky|ohio)|birmingham,? (al|alabama|mi)|"
                       r"cambridge,? (ma|massachusetts)|manchester,? (nh|ct)|reading,? pa|york,? pa|"
                       r"aberdeen,? (sd|md|wa)|bath,? (me|ny)|newcastle,? (nsw|australia)|perth|"
                       r"wales,? (nsw|australia)|new south wales", re.I)


def is_uk(*parts):
    s = " | ".join(p for p in parts if p)
    if not s:
        return False
    if _FALSE_UK.search(s) and not re.search(r"united kingdom|\buk\b", s, re.I):
        return False
    return bool(_UK_RX.search(s))


def is_london(s):
    return bool(re.search(r"london|canary wharf|city of london", s or "", re.I))
