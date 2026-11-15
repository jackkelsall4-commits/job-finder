"""Text helpers: HTML stripping and UK location detection."""
import html
import re

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(s, limit=15000):
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
                       r"wales,? (nsw|australia)|new south wales|new london", re.I)


def is_uk(*parts):
    s = " | ".join(p for p in parts if p)
    if not s:
        return False
    if _FALSE_UK.search(s) and not re.search(r"united kingdom|\buk\b", s, re.I):
        return False
    return bool(_UK_RX.search(s))


def is_london(s):
    return bool(re.search(r"london|canary wharf|city of london", s or "", re.I))


US_WORDS = [
    "united states", "new york", "nyc", "manhattan", "brooklyn", "jersey city", "hoboken", "chicago", "boston",
    "san francisco", "menlo park", "palo alto", "greenwich", "stamford", "miami", "west palm beach", "philadelphia",
    "houston", "dallas", "austin", "charlotte", "los angeles", "seattle", "denver", "atlanta", "washington, dc",
    "washington dc", "minneapolis", "salt lake city", "princeton", "baltimore", "pittsburgh", "detroit", "phoenix",
    "san diego", "nashville", "tampa", "columbus", "cleveland", "st. louis", "kansas city", "wilmington", "jacksonville",
]
_US_RX = re.compile(r"\b(" + "|".join(re.escape(w) for w in US_WORDS) + r")\b|\busa\b|\bu\.s\.a?\b", re.I)
# "Chicago, IL", "US-NY-New York", "Remote - US": state codes and a bare US are matched case-sensitively.
_US_CODES = re.compile(r"(?:,\s*|\bUS[-, ]+)(NY|NJ|IL|MA|CA|TX|CT|FL|PA|NC|GA|WA|CO|DC|MN|UT|AZ|OH|MI|VA|MD|TN|MO|DE)\b|\bUSA?\b")


def is_us(*parts):
    s = " | ".join(p for p in parts if p)
    if not s or re.search(r"london,? (ontario|on\b)|new york,? (lincolnshire|uk)", s, re.I):
        return False
    return bool(_US_RX.search(s) or _US_CODES.search(s))


def region_of(*parts):
    """"UK", "US" or None. A posting listed in both (e.g. "London / New York") counts as UK."""
    s = " | ".join(p for p in parts if p)
    if is_uk(s) or (re.search(r"(?<!new )\blondon\b(?!,? (ontario|on\b|ky|kentucky|ohio))|canary wharf|edinburgh|glasgow|england|scotland", s, re.I)
                    and re.search(r"[/;|,]|\b(also|or|and)\b", s)):
        return "UK"
    if is_us(*parts):
        return "US"
    return None


def in_scope(*parts):
    return region_of(*parts) is not None


# Big US finance centres, where pay runs at or above London levels.
US_HUBS = re.compile(r"new york|nyc|manhattan|jersey city|hoboken|chicago|boston|san francisco|menlo park|palo alto|"
                     r"greenwich|stamford|miami|west palm|\bny\b", re.I)
