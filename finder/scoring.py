"""Scores each vacancy for: pay, how reachable it is for this candidate now,
fit with the target areas, and work-from-home options.

All pay figures are annual GBP (US dollar pay is converted, and the dollar figure kept).
When an advert shows no salary, pay is estimated from the employer type and the role's
seniority using the bands below (rough London market levels for 2025-26: base plus
typical bonus; big US finance centres run higher). They are deliberately round numbers;
edit them if you know better.

Every role also gets a one-line summary taken from the advert's own words, the parts of the
advert that play to the candidate's experience ("pros") and the gaps ("flags"). All of it is
plain pattern matching on the advert text - nothing is guessed from the title alone.
"""
import re

from .text import is_london, region_of, US_HUBS

# GBP per USD. run.py replaces this with the day's rate when it can fetch one.
USD_TO_GBP = 0.75
# US pay against the London bands above, in GBP terms.
US_PAY = {"hub": 1.35, "other": 1.15}

LEVELS = ["apprentice", "intern", "entry", "mid", "senior", "exec"]

# Typical total compensation (base + bonus), London, £k, front-office roles.
BANDS = {
    #               apprentice intern entry mid  senior exec
    "prop_trading":  (45, 90, 170, 260, 380, 600),
    "hedge_fund":    (35, 70, 110, 170, 260, 450),
    "pe_megafund":   (35, 70, 120, 210, 320, 600),
    "pe_midmarket":  (30, 50, 75, 120, 190, 320),
    "bulge_bracket": (32, 60, 100, 160, 260, 450),
    "boutique":      (30, 60, 105, 165, 270, 480),
    "big4":          (26, 35, 38, 58, 85, 140),
    "mid_tier":      (24, 30, 34, 52, 75, 120),
    "asset_manager": (28, 40, 52, 80, 120, 200),
    "retail_bank":   (25, 32, 40, 60, 85, 140),
    "fintech":       (28, 40, 52, 80, 110, 170),
    "other":         (23, 28, 34, 48, 68, 105),
}
# Pay multiplier for support functions (ops, control, compliance...) by employer type.
BACK_OFFICE = {"prop_trading": .35, "hedge_fund": .4, "pe_megafund": .35, "pe_midmarket": .55, "bulge_bracket": .45,
               "boutique": .45, "big4": .92, "mid_tier": .92, "asset_manager": .7, "retail_bank": .85,
               "fintech": .85, "other": .95}
# Typical bonus on top of an advertised base salary.
BONUS = {"prop_trading": .8, "hedge_fund": .5, "pe_megafund": .5, "pe_midmarket": .3, "bulge_bracket": .35,
         "boutique": .4, "big4": .05, "mid_tier": .05, "asset_manager": .2, "retail_bank": .1,
         "fintech": .1, "other": .05}
ELITE = {"prop_trading", "hedge_fund", "pe_megafund", "pe_midmarket", "bulge_bracket", "boutique"}
CATEGORY_LABEL = {
    "prop_trading": "Prop trading / market maker", "hedge_fund": "Hedge fund", "pe_megafund": "Private equity (large)",
    "pe_midmarket": "Private equity (mid-market)", "bulge_bracket": "Investment bank", "boutique": "IB boutique / advisory",
    "big4": "Big 4", "mid_tier": "Mid-tier advisory", "asset_manager": "Asset manager / markets infra",
    "retail_bank": "UK bank", "fintech": "Fintech / broker", "other": "Other employer",
}

# ---------------------------------------------------------------- interest areas
TRACKS = [
    ("Trading", 1.0, r"\btrad(er|ers|ing)\b|sales ?& ?trading|global markets|markets (analyst|associate|graduate|apprentic|division)|"
                     r"market mak|\bdesk\b|\bfx\b|\brates\b|commodit|derivativ|e-?trading|structur(ed|ing)"),
    ("Investing", 1.0, r"invest(ment|ing|or)? (analyst|associate|team|professional|management)|portfolio manag|"
                       r"asset manag|equity research|credit research|research analyst|hedge fund|"
                       r"\bfund manag|buy[- ]side|long/short|\bpm\b"),
    ("Corporate finance / IB", 1.0, r"corporate finance|m ?& ?a\b|mergers|acquisitions|investment bank|"
                                    r"\bibd?\b|coverage|capital markets|\becm\b|\bdcm\b|leveraged finance|"
                                    r"lev ?fin|origination|advisory|corporate broking|financial sponsors"),
    ("Transaction services", 1.0, r"transaction services|transaction advisory|due diligence|\bfdd\b|"
                                  r"deal advisory|\bdeals?\b|valuations?\b|restructuring|turnaround|"
                                  r"lead advisory|financial advisory|forensic"),
    ("Private equity", 1.0, r"private equity|\bpe\b|buy-?out|portfolio compan|growth equity|"
                            r"venture capital|private capital|private markets|private credit|direct lending"),
    ("ACA stepping stone", .55, r"fund (accountant|accounting|finance|controller)|fund services|product control|"
                                r"valuation control|financial control|financial reporting|\bfp&a\b|treasury|"
                                r"credit analyst|corporate development|internal audit|\baca\b|"
                                r"\bacca\b|part[- ]qualified|chartered accountant"),
    ("Finance / accounting", .35, r"financ|account|audit|\btax\b|controller|analyst|actuar"),
    ("Ops / risk", .22, r"operations|\bops\b|risk|compliance|\bkyc\b|\baml\b|settlement|middle office"),
]
TRACKS = [(n, w, re.compile(rx, re.I)) for n, w, rx in TRACKS]
TECH = re.compile(r"engineer|developer|software|devops|\bsre\b|infrastructure|data scien|machine learning|"
                  r"cyber|security|\bit\b|technolog|desktop|help ?desk|service desk|cloud|platform|architect|network|systems|java|python|c\+\+|"
                  r"quant(itative)? (dev|research)|researcher|fpga|hardware|kdb|\bdba\b|database|postgres|\bsql\b|\biam\b|"
                  r"identity (and|&) access|csirt|\bsoc\b|incident response|application support", re.I)
NON_FIN = re.compile(r"\b(hr|human resources|people partner|recruit\w*|talent|front desk|meteorolog\w*|scientist|social media|team assistant|office assistant|legal|counsel|paralegal|marketing|"
                     r"communications|customer experience|branch|cashier|contact centre|account executive|reporter|journalist|editor|lawyer|solicitor|attorney|designer|office manager|receptionist|facilities|executive assistant|"
                     r"personal assistant|administrator|chef|cleaner|driver|nurse|teacher|customer service|"
                     r"call centre|retail assistant|barista|warehouse|hospitality|coffee|food service|catering|concierge|"
                     r"housekeeping|mailroom|interest form|discovery event|chess academy|high school|pact \d{4}|conference|"
                     r"administrative (assistant|support|coordinator)|admin assistant|register your interest|expression of interest|"
                     r"general application|proposal writer|bids? (executive|writer|manager|coordinator)|training specialist|"
                     r"financial guide)\b", re.I)


def interest_of(title, text):
    """(score 0-1, matched track names). Title counts fully; body text is capped."""
    title = title or ""
    if NON_FIN.search(title):
        return 0.02, []
    if TECH.search(title) and not re.search(r"\btrader\b|investment bank|private equity|\bdeals?\b", title, re.I):
        return 0.05, ["Technology"]
    hits, best = [], 0.0
    for name, w, rx in TRACKS:
        if rx.search(title):
            hits.append(name)
            best = max(best, w)
    if text:
        for name, w, rx in TRACKS[:6]:
            if name not in hits and len(rx.findall(text)) >= 2:
                hits.append(name)
                best = max(best, min(w, .5))
    return best, hits


# ---------------------------------------------------------------- seniority & function
def level_of(title, dept=""):
    t = (title or "").lower()
    if re.search(r"\bintern(ship)?s?\b|placement|summer|spring (week|insight)|insight (week|programme|day)|"
                 r"off[- ]cycle|industrial|work experience|year in industry|\bco-?op\b", t):
        return "intern"
    if re.search(r"apprentic|school leaver", t) or dept == "Apprenticeship":
        return "apprentice"
    if re.search(r"managing director|\bmd\b|\bpartner\b|chief|\bhead\b|global head|"
                 r"(?<!associate )(?<!executive )(?<!assistant )director", t):
        return "exec"
    if re.search(r"vice president|\b[as]?vp\b|senior manager|(?<!assistant )manager\b|\blead\b|principal|"
                 r"associate director|executive director|assistant director|team leader", t):
        return "senior"
    if re.search(r"assistant manager|\bavp\b|assistant vice president|\bsenior\b", t):
        return "mid"
    if re.search(r"rotational|\b20\d\d (start|program)|program(me)?:? (january|june|july|august|september|fall|summer) 20\d\d|"
                 r"graduates?|\bgrads?\b|junior|trainee|entry|new grad|campus|\banalyst\b(?!.*senior)|\bassistant\b|\bexecutive\b", t) \
            and not re.search(r"senior|\bassociate\b(?!.*analyst)", t):
        return "entry"
    if re.search(r"analyst ?/ ?associate|associate ?/ ?analyst", t):
        return "entry"
    return "mid"


def function_of(title, dept=""):
    s = f"{title} | {dept}"
    if re.search(r"trade support|operations|\bops\b|settlement|reconcil|compliance|\bkyc\b|\baml\b|onboarding|"
                 r"control(ler)?\b|administrat|coordinator|middle office|back office|fund account|fund services|"
                 r"accountant|\bap\b|payroll|accounts (payable|receivable)|bookkeep|credit control|market data|"
                 r"\bpmo\b|project manag|business manag|reporting", s, re.I):
        return "back"
    front_title = any(rx.search(title or "") for _, _, rx in TRACKS[:5])
    if front_title and not re.search(r"engineer|developer|software|devops|\bsre\b", title or "", re.I):
        return "front"
    if TECH.search(title or ""):
        return "tech"
    if any(rx.search(s) for _, _, rx in TRACKS[:5]):
        return "front"
    return "other"


# ---------------------------------------------------------------- salary parsing
_MONEY = {"GBP": re.compile(r"(?:£|\bgbp\s?)\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(k\b)?", re.I),
          "USD": re.compile(r"(?:\$|\busd\s?)\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(k\b)?", re.I)}
_PAY_WORDS = re.compile(r"salary|base pay|base compensation|pay range|compensation|annual(ly)?|hiring range|wage|\bpay\b|\bbase\b", re.I)


def find_pay_text(desc, currency):
    """US adverts often put the legally required pay range near the end: find it anywhere."""
    for m in _MONEY[currency].finditer(desc or ""):
        if _PAY_WORDS.search(desc[max(0, m.start() - 160):m.start()]):
            return desc[m.start():m.start() + 90]
    return ""


def parse_salary(text, currency="GBP"):
    """Return (min, max) annual pay in the advert's currency from text such as
    '£45,000 - £55,000 a year', '£500 a week' or '$150,000 - $200,000'."""
    if not text:
        return None, None
    vals = []
    for m in list(_MONEY[currency].finditer(text))[:3]:
        v = float(m.group(1).replace(",", ""))
        if m.group(2):
            v *= 1000
        after = text[m.end(): m.end() + 30].lower()  # the pay period follows the figure
        if re.search(r"per hour|an hour|/ ?h(ou)?r|hourly|p/?h\b", after):
            v *= 1950
        elif re.search(r"per day|a day|/ ?day|daily|p/?d\b", after):
            v *= 230
        elif re.search(r"per week|a week|/ ?week|weekly|p/?w\b", after):
            v *= 52
        elif re.search(r"per month|a month|/ ?month|monthly|pcm", after):
            v *= 12
        if 8_000 <= v <= 1_500_000:
            vals.append(v)
    if not vals:
        return None, None
    vals = vals[:2]
    return min(vals), max(vals)


# ---------------------------------------------------------------- employer classification
def classify(company, aliases):
    c = f" {(company or '').lower()} "
    for cat, names in aliases.items():
        if cat.startswith("_"):
            continue
        for n in names:
            if re.search(r"(?<![a-z0-9])" + re.escape(n.strip()) + r"(?![a-z0-9])", c):
                return cat
    return "other"


# ---------------------------------------------------------------- work-from-home
def workplace_of(job):
    if job.get("workplace"):
        w = job["workplace"].lower()
        if "remote" in w:
            return "remote"
        if "hybrid" in w:
            return "hybrid"
        if "site" in w or "office" in w:
            return "office"
    s = " ".join([job.get("title", ""), job.get("location", ""), job.get("description", "")]).lower()
    if re.search(r"fully remote|remote[- ]first|100% remote|work from home|home[- ]based|remote \(uk\)|uk remote|"
                 r"remote working|\bremote\b", s):
        if re.search(r"hybrid|days? (a|per) week in (the )?office|in the office \d", s):
            return "hybrid"
        return "remote"
    if re.search(r"hybrid|flexible working|agile working|days? (a|per) week in (the )?office|work from home \d", s):
        return "hybrid"
    if re.search(r"office[- ]based|on[- ]site|onsite|5 days (a|per) week in|fully in[- ]office", s):
        return "office"
    return "unknown"


# ---------------------------------------------------------------- requirements vs this candidate
# Reach is scored twice: as the candidate is today, and once the ACA is complete. The profile can
# override "experience_years" (relevant work experience today) and "years_to_aca".
def stages(profile):
    now = float(profile.get("experience_years", 2))
    return {"now": {"years": now, "aca": False},
            "aca": {"years": now + float(profile.get("years_to_aca", 2)), "aca": True}}


_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_N = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
_YEARS = re.compile(r"\b" + _N + r"\s*(?:\+|plus)?\s*(?:(?:-|–|to)\s*" + _N + r"\s*)?\+?\s*(?:or more\s+)?(?:years?|yrs)\b", re.I)
_EXP_AFTER = re.compile(r"^[’'s\s]*(?:\+\s*)?(?:of\s+)?(?:(?:relevant|professional|proven|practical|hands-on|demonstrable|"
                        r"previous|prior|industry|work|working|post[- ]?qualifi\w*|pqe)\s+)*"
                        r"(?:experience|exp\b|in\b|within\b|at\b|as\b|working|pqe|post[- ]?qual)"
                        r"|^[’'s\s]*(?:\+\s*)?of\s+(?:[\w,/&()-]+\s+){0,8}?(?:experience|exp\b)", re.I)
_EXP_BEFORE = re.compile(r"(minimum of|at least|experience of|experience:|requires?|you have|you'll have|with)\s*$", re.I)
_NOT_REQ = re.compile(r"\b(we|we've|our|firm|company|founded|history|over the (past|last)|for over|ago|age|aged|"
                      r"next|every|each|term|contract|fixed)\b[^.]{0,25}$", re.I)
# Experience where two years of audit count in full.
_RELEVANT_EXP = re.compile(r"audit|accountan|accounting|assurance|financial (reporting|statements|control|accounting)|"
                           r"finance (function|team)|professional services|big ?(4|four)|practice|controls? testing|"
                           r"reconcil|regulatory reporting|statutory|qualified|pqe|post[- ]?qual|\bfinance\b|"
                           r"quantitative or technical|analytical|professional experience|relevant experience|"
                           r"work experience|related (role|field|experience)", re.I)
# Experience audit only partly stands in for (operations, risk, control-type work).
_NEAR_EXP = re.compile(r"client services|settlement|middle office|back office|operations|\bops\b|risk|compliance|\baml\b|"
                       r"\bkyc\b|product control|treasury|fund admin|fund account|valuation|due diligence|transaction services|"
                       r"credit|insurance|tax\b|lending|wealth|data\b|reporting|modelling|fp&a|business analys", re.I)
# Experience audit doesn't give at all.
_FAR_EXP = re.compile(r"trad(e|er|ers|ing)\b|forecast|software|engineer|develop(er|ment)|sales|portfolio manag|"
                      r"investment bank|m ?& ?a\b|mergers|corporate finance|private equity|principal invest|\bdeals?\b|"
                      r"leveraged finance|capital markets|quant|research|legal|marketing|recruit|python|coding|"
                      r"programming|energy|commodit|fixed income|derivativ|equities|actuar|buy[- ]side|hedge fund|"
                      r"asset management|investing|investment (team|analyst|experience)", re.I)


def years_required(low):
    """Every experience requirement in the advert: [(years, post-qualification?, context, credit)] where
    credit is how much of the candidate's audit experience counts (1, .5 or 0)."""
    out = []
    for m in _YEARS.finditer(low):
        n = m.group(1)
        n = int(n) if n.isdigit() else _WORDNUM[n]
        pre, post = low[max(0, m.start() - 60):m.start()], low[m.end():m.end() + 140]
        if n == 0 or n > 15 or _NOT_REQ.search(pre[-40:]):
            continue
        if not (_EXP_AFTER.search(post) or (_EXP_BEFORE.search(pre) and "experience" in post[:90])):
            continue
        ctx = re.split(r"[.;•·]", post)[0][:110].strip(" '+s")
        ctx = re.sub(r"^(of\s+)?((relevant|professional|proven|work|working|industry)\s+)*(experience\s+)?"
                     r"((in|of|with|within|across)\s+)?", "", ctx)
        ctx = re.split(r"\s(?:and|with|including|plus)\s(?:at least|a minimum|minimum|strong|excellent|proven)\b", ctx)[0]
        pqe = bool(re.search(r"pqe|post[- ]?qualif|since qualif|qualified experience", pre[-30:] + post[:60]))
        credit = _credit(ctx)
        out.append((n, pqe, ctx, credit))
    return out


# "Skills you'll need: experience in a trading operations role" - specific experience with no years given.
_NEEDS_EXP = re.compile(r"(?:(you have|you'll have|you will have|you'll need|you need|you must have|must have|required|requirements|"
                        r"essential|skills and experience|what you need|what you'll bring|you bring|you'll bring|ideal candidate)"
                        r"\W{0,4}(?:significant |proven |strong |solid |demonstrable |relevant |prior |previous |direct |hands-on )*"
                        r"|\b(?:previous|prior|proven|demonstrable|direct|hands-on|solid|strong|significant|extensive|substantial|deep|relevant) )"
                        r"(?:[\w-]+ ){0,3}?experience (?:(?:gained|acquired|built|working) )?(?:in|of|with|within|across|at)\s+([^.;•]{0,90})", re.I)
# Roles that need an existing client book, deal leadership or people management.
_BOOK = re.compile(r"book of business|portfolio of clients|client book|established (network|relationships|client)|"
                   r"deep (industry|client) relationships|lead coverage|assume (lead )?coverage|track record of (generating|originating|winning|"
                   r"bringing in)|originat\w* (new )?(business|clients|mandates)", re.I)
_LEADS = re.compile(r"track record of leading|managing junior|manage junior|leading (live )?(deals|transactions)|lead(ing)? bond transactions|"
                    r"line management|people management experience|managing (a|the) team of|experience (of |in )?managing (a )?team", re.I)

# Coding. The candidate can't write code yet, so a hard Python/SQL requirement is a big gap at either stage.
_CODE = re.compile(r"\b(python|sql|vba|c\+\+|c#|java|javascript|kdb\+?|powershell|programming languages?|coding|scripting)(?![\w+#])", re.I)
_CODE_SOFT = re.compile(r"\b(a plus|plus\b|desirable|advantag\w*|nice to have|we'd love|bonus|prefer\w*|willing to learn|basic|"
                        r"ideally|beneficial|exposure|would be|helpful|a benefit|interest\w*|keen to|appetite|not required|"
                        r"not essential|or similar tools|curious|great fit|might be a|could be a)\b", re.I)


def coding_need(low):
    """'hard' if the advert needs real coding, 'soft' if it's only a plus, else None."""
    found = None
    for m in _CODE.finditer(low):
        a = max(0, low.rfind(".", 0, m.start()), low.rfind("•", 0, m.start()), m.start() - 250)
        b = min([x for x in (low.find(".", m.end()), low.find("•", m.end())) if x != -1] + [m.end() + 160])
        if _CODE_SOFT.search(low[a:b]):
            found = found or "soft"
        else:
            return "hard", m.group(1)
    return (found, None) if found else (None, None)


def _credit(ctx):
    """How much audit experience counts towards experience described by ctx: 1, .5 or 0."""
    if re.search(r"audit|accountan|accounting|assurance|financial (reporting|statements|control)|big ?(4|four)|"
                 r"reconcil|quantitative or technical", ctx):
        return 1
    related = re.search(r"related (role|field|area|discipline)|similar (role|field)", ctx)
    if _FAR_EXP.search(ctx):
        return .5 if related else 0
    if _NEAR_EXP.search(ctx):
        return .75 if related else .5
    if not ctx or related or _GENERIC_EXP.match(ctx):
        return 1
    return .5  # experience in some other named field (consulting, corporate actions...): audit only partly stands in


_GENERIC_EXP = re.compile(r"^((is |are )?(preferred|required|desirable|essential|needed|ideal)|would|will|or more|experience\b|"
                          r"professional|relevant|work|working|industry|full[- ]time|post|of\b|and\b|"
                          r"(in|within|at) (a|an) (professional|similar|relevant|fast|busy|office|corporate|client|large|global|team)|"
                          r"a (professional|corporate|similar|relevant|business))", re.I)


_LANGS = (r"japanese|mandarin|cantonese|chinese|french|german|spanish|portuguese|italian|arabic|korean|russian|dutch|"
          r"hindi|turkish|polish|hebrew|swedish|norwegian|danish|greek")


def _soft_years(ctx):
    return bool(re.search(r"preferred|not required|desirable|a plus", ctx or ""))


def _short(t, n=60):
    t = t.strip(" ,")
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0].rstrip(" ,") + "…"


def _gap_factor(gap):
    if gap <= 0:
        return 1.0
    return .6 if gap <= 1 else .3 if gap <= 2 else .15 if gap <= 3 else .07


_SOFT = re.compile(r"^[^.]{0,40}\b(preferred|preferable|desirable|advantage|advantageous|a plus|nice to have|bonus|beneficial)\b", re.I)
_SOFT_BEFORE = re.compile(r"\b(ideally|preferably|desirable|nice to have|bonus|plus)\b[^.]{0,30}$", re.I)
_ADV_DEGREE = re.compile(r"\b(master'?s(?! (of|in) (none|excel))|msc|m\.sc|mba|ph\.?d|doctorate|doctoral|advanced degree|"
                         r"postgraduate (degree|qualification))\b", re.I)
_DEGREE = re.compile(r"\bbachelor|degree or equivalent|(undergraduate|university|third[- ]level|3rd[- ]level|college) degree|degree[- ](level|educated)|"
                     r"educated to degree|tertiary (qualification|degree|education)|\b2[:.]1\b|degree (is )?(required|essential)|hold a degree|have a degree|"
                     r"(a|relevant|strong) degree in|\bbsc\b|\bb\.?a\.? in\b|\bbs\b (in|degree)|degree in (finance|economics|"
                     r"accounting|business|math)", re.I)
_STEM_DEGREE = re.compile(r"degree[^.]{0,30}?\b(in|of)\b([^.]{0,90})", re.I)
_STEM = re.compile(r"meteorolog|atmospheric|physics|mathemat|\bmaths\b|computer science|engineering|statistics|chemistry|"
                   r"biology|\blaw\b|medicine|data science|machine learning|actuarial science", re.I)
_FIN_SUBJ = re.compile(r"financ|account|econom|business", re.I)
_NO_DEGREE = re.compile(r"(don'?t|do not|doesn'?t|does not) (need|require|have to have) a degree|with or without a degree|"
                        r"degree (is )?not (required|necessary|essential|needed)|no degree (required|needed|necessary)|"
                        r"regardless of (your )?degree", re.I)
# Specialist qualifications the candidate doesn't hold (AAT/ACA don't cover them).
_OTHER_QUAL = re.compile(r"\b(m?rics)\b|chartered surveyor|qualified (real estate )?valu\w* professional|statement of professional standing|"
                         r"\bdippfs\b|diploma in (regulated )?financial planning|chartered financial planner|"
                         r"qualified financial (adviser|advisor|planner)|\bcta[- ]qualified|qualified cta\b|\batt[- ]qualified|"
                         r"cfa charter ?holder", re.I)
_OTHER_QUAL_NAME = [("rics", "RICS (chartered surveyor)"), ("surveyor", "RICS (chartered surveyor)"),
                    ("valu", "a property-valuation (RICS) qualification"), ("professional standing", "a regulated financial-adviser qualification"),
                    ("financial planning", "a regulated financial-adviser qualification"), ("dippfs", "a regulated financial-adviser qualification"),
                    ("planner", "a regulated financial-adviser qualification"), ("adviser", "a regulated financial-adviser qualification"),
                    ("advisor", "a regulated financial-adviser qualification"), ("cta", "a tax qualification (CTA)"),
                    ("att", "a tax qualification (ATT)"), ("cfa", "the CFA charter")]
_QUALIFIED = re.compile(r"newly[- ]qualified|\bnq\b|fully[- ]qualified|qualified \(?(aca|acca|cima|cpa|accountant|chartered|auditors?|"
                        r"(audit|assurance) (or (audit|assurance) )?professional)|"
                        r"\b(aca|acca|cima|cpa|icaew)\b[^.]{0,30}?\bqualified\b|chartered accountan(t|cy)|post[- ]qualification|"
                        r"\bpqe\b|qualified accountant|(accountancy|accounting) qualification( or equivalent)?( is)? (required|essential)|"
                        r"(aca|acca|cima|cpa)( or (aca|acca|cima|cpa|equivalent))*,? (is )?(required|essential)|"
                        r"must be (aca|acca|a qualified|a chartered)|professionally qualified", re.I)
_PART_QUALIFIED = re.compile(r"part[- ]qualified|studying (towards|for)|working towards|qualified or (part|studying|nearly)|"
                             r"\bqbe\b|qualified by experience|nearly qualified|finalist", re.I)


# ---------------------------------------------------------------- what the role is (advert's own words)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])|\s*[•·▪●]\s*|\n+")
_ROLE_RX = [re.compile(rx, re.I) for rx in (
    r"\b(we (require|need|are looking for|are seeking|are hiring|seek|are recruiting)|we're (looking for|seeking|hiring|recruiting)|"
    r"(is|are) (looking|seeking|hiring) (for )?(an?|the) )",
    r"\bin this role,? you\b|\bas (an?|the|our) [^,.]{3,70}, you('ll| will)\b|"
    r"\byou (will|'ll) (be responsible|be joining|join|work|support|lead|help|own|manage|provide|play|sit|report|be working)",
    r"\b(this|the) (role|position|opportunity|job) (is|will|involves|sits|offers|focuses|requires|supports|reports)\b",
    r"\b(role|position|job) (overview|summary|purpose|description)\b|\bpurpose of the role\b|\babout the (role|job|position)\b|"
    r"\bthe opportunity\b|\bwhat you('ll| will) (do|be doing)\b|\byour role\b|\bresponsibilities\b",
)]
_HEADER = re.compile(r"^(job description|description|job summary|overview|about the (role|job|position)|the role|role overview|"
                     r"purpose of the role|position summary|role summary|the opportunity|your role|what you('ll| will) (do|be doing)|"
                     r"responsibilities|key responsibilities)\s*[:\-–]?\s*", re.I)
_BOILER = re.compile(r"\b(founded|is a leading|is one of the|equal opportunit|headquartered|our (mission|culture|values|people)|"
                     r"we are (a|an|the|one)|we're (a|an|the|one)|award[- ]winning|diversity|inclusion|privacy|cookies|"
                     r"benefits|recruitment agenc|reasonable adjustment|salary|compensation|pay range|start date|timelines?|"
                     r"exciting opportunity|highly motivated|apply now|closing date)\b|[$£]\s?\d", re.I)


def _clip(t, n):
    t = t.strip(" -–:;,")
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


_STOP = {"and", "the", "of", "for", "to", "in", "a", "an", "&", "-", "–", "with", "on", "at", "or", "uk", "london", "new",
         "york", "senior", "junior", "associate", "analyst", "manager", "assistant", "vice", "president", "vp", "avp",
         "director", "lead", "head", "officer", "specialist", "team", "role", "2025", "2026", "2027", "programme", "program"}
_BOILER2 = re.compile(r"if you are looking for|if you're looking for|our roles generate|working with us|close (for )?applications|"
                      r"place for you|build a future|make an impact|true self|individuality|why join|life at|join us\b|"
                      r"great place to work|we offer|we provide|you('ll| will) (enjoy|receive|get)|hybrid working|flexible working|"
                      r"\d[\d,]* (people|employees|colleagues)|countries|years of (history|heritage)|our clients include", re.I)


def _boiler(x, title_low=""):
    """Company blurb / pay / HR wording - unless the word is the job's own subject (a Compensation Analyst's advert says "compensation")."""
    if _BOILER2.search(x):
        return True
    return any(m.group(0).lower() not in title_low for m in _BOILER.finditer(x))


def role_summary(desc, title="", n=210):
    """The advert's own sentence(s) on what the job is; '' if the advert text is too short to tell.
    Picks the early sentence that best describes the job: role wording ("you will", "we are looking for")
    and the title's own words count for it, company blurb counts against it."""
    if not desc or len(desc) < 120:
        return ""
    tl = (title or "").lower()
    words = {w for w in re.findall(r"[a-z&]+", tl) if w not in _STOP and len(w) > 2}
    sents = [x.strip() for x in _SENT_SPLIT.split(desc[:9000]) if x and len(x.strip()) > 25][:45]
    best, best_score = None, 0
    for i, x in enumerate(sents):
        body = _HEADER.sub("", x)
        if not 40 <= len(body) <= 600 or _boiler(x, tl):
            continue
        sc = sum(3 - k * .5 for k, rx in enumerate(_ROLE_RX) if rx.search(x))
        sc += 1.5 * min(3, sum(1 for w in words if re.search(r"\b" + re.escape(w), x.lower())))
        sc -= i * .04  # earlier is better
        if sc > best_score:
            best, best_score = i, sc
    if best is None:
        best = next((i for i, x in enumerate(sents) if len(x) >= 50 and not _boiler(x, tl)), None)
    if best is None:
        return ""
    out = _HEADER.sub("", sents[best])
    out = re.sub(r"^(purpose of the role|about the role|the role)\s+", "", out, flags=re.I)
    nxt = sents[best + 1] if best + 1 < len(sents) else ""
    if len(out) < 120 and nxt and not _boiler(nxt, tl):
        out = out.rstrip(".") + ". " + _HEADER.sub("", nxt)
    out = out[0].upper() + out[1:] if out else out
    return _clip(out, n)


def _sentence(low, start, end, before=220, after=160):
    """The sentence or bullet around low[start:end]."""
    a = max(low.rfind(". ", max(0, start - before), start), low.rfind("•", max(0, start - before), start),
            low.rfind("\n", max(0, start - before), start), start - before, 0)
    nxt = [x for x in (low.find(". ", end), low.find("•", end), low.find("\n", end)) if x != -1]
    b = min(nxt + [end + after])
    return low[a:b]


# ---------------------------------------------------------------- specialist systems the candidate hasn't used
SYSTEMS = [(n, re.compile(rx, re.I)) for n, rx in (
    ("Oracle", r"\boracle\b(?! (corporation|park|arena))|\bebs r12\b|\botbi\b"),
    ("SAP", r"\bsap\b|s/?4 ?hana"),
    ("Workday Financials", r"workday (financials|adaptive|prism|accounting)|adaptive insights"),
    ("Hyperion/HFM", r"hyperion|\bhfm\b|essbase"),
    ("OneStream", r"onestream"), ("BlackLine", r"blackline"), ("Anaplan", r"anaplan"), ("NetSuite", r"netsuite"),
    ("Coupa", r"\bcoupa\b"), ("Tagetik", r"tagetik"), ("Workiva", r"workiva"), ("Kyriba", r"kyriba"),
    ("Murex", r"\bmurex\b"), ("Calypso", r"\bcalypso\b"), ("Aladdin", r"\baladdin\b"), ("SimCorp", r"simcorp"),
    ("Advent Geneva", r"advent geneva|\bgeneva\b(?=[^.]{0,25}(system|platform|accounting))"),
    ("Investran", r"investran"), ("eFront", r"\befront\b"), ("Allvue", r"allvue"), ("Yardi", r"\byardi\b"),
    ("Charles River", r"charles river (ims|development)"), ("Temenos", r"temenos"),
    ("AxiomSL", r"axiom ?sl\b|axiom (cv|controllerview)"),
    ("Microsoft Dynamics", r"dynamics 365|\bd365\b|navision|business central"), ("Salesforce", r"salesforce"),
)]
_SYS_REQ = re.compile(r"experience|expertise|knowledge|proficien|hands[- ]on|skilled|specialist|expert|implement|configur|"
                      r"functional|consultant|certif|background|familiar|working with|track record|understanding", re.I)
_SYS_DEEP = re.compile(r"\b(deep|extensive|expert|in-depth|advanced|strong|proven|significant|hands[- ]on|certified|\d+\+? ?years?)\b", re.I)
_SYS_GENERIC = re.compile(r"such as|e\.g|\beg\b|for example|or similar|or equivalent|or other|any (major )?erp|\blike\b|i\.e", re.I)
_SYS_SOFT = re.compile(r"\b(a plus|plus\b|desirable|advantag\w*|nice to have|bonus|prefer\w*|ideally|beneficial|exposure|"
                       r"helpful|a benefit|not (required|essential)|highly valued|valued)\b", re.I)
_SYS_RANK = {"title": 0, "deep": 1, "req": 2, "generic": 3, "soft": 4}
_SYS_MULT = {"title": .25, "deep": .4, "req": .55, "generic": .85, "soft": .95}


def systems_need(title, low, known=()):
    """(kind, [system names]) for the hardest specialist-system demand in the advert, or (None, [])."""
    known = {k.lower() for k in known}
    hits = []
    for name, rx in SYSTEMS:
        if name.lower() in known:
            continue
        if rx.search(title or ""):
            hits.append(("title", name))
            continue
        for m in rx.finditer(low):
            sent = _sentence(low, m.start(), m.end())
            if _SYS_GENERIC.search(sent):
                kind = "generic"
            elif _SYS_SOFT.search(sent):
                kind = "soft"
            elif _SYS_REQ.search(sent):
                kind = "deep" if _SYS_DEEP.search(sent) else "req"
            else:
                continue
            hits.append((kind, name))
            break
    if not hits:
        return None, []
    worst = min(hits, key=lambda h: _SYS_RANK[h[0]])[0]
    return worst, [n for k, n in hits if k == worst][:3]


# ---------------------------------------------------------------- what in the advert plays to an audit background
_ASKS = re.compile(r"experience|background|knowledge|understanding|exposure|familiar|skills?\b|you (have|bring|will have|'ll have|'ll bring)|"
                   r"candidate|ideally|essential|required|requirements|preferred|desirable|qualifi|track record|proficien|"
                   r"demonstrat|grounding|expertise|comfortable", re.I)
# Where the advert's requirements start; strengths found after this count without a sentence check.
_REQ_HEAD = re.compile(r"essential (skills|criteria|requirements)|you must have|requirements:|what you('ll| will)? need|what we('re| are) looking for|"
                       r"skills (and|&) experience|experience (and|&) skills|qualifications:|about you|to be successful|"
                       r"the ideal candidate|who you are|what you('ll| will)? bring|you will have|you('ll| will) need|your (skills|experience|profile)|"
                       r"basic qualifications|preferred qualifications|minimum qualifications|requirements of the role|key requirements|"
                       r"candidate profile|person specification", re.I)
_STRENGTH_SKIP = re.compile(r"may be assessed|critical skills relevant|all colleagues will be expected", re.I)
STRENGTHS = [(k, re.compile(rx, re.I)) for k, rx in (
    ("audit", r"\b(audit(ing)? (experience|background|firm|practice|requirements|skills)|external audit|statutory audit|"
              r"big ?(4|four)|(audit|assurance) (and|&) (assurance|advisory)|assurance|(?<!internal )audit(?! (committee|evidence|trail|log)))\b"),
    ("reporting", r"financial (reporting|statements|accounts)|statutory (accounts|reporting)|\bifrs\b|\b(uk |us )?gaap\b|"
                  r"(year|period|month|quarter)[- ]end( close| reporting| processes)?|financial close"),
    ("controls", r"\bsox\b|sarbanes|internal controls?|controls? testing|control (framework|environment)|risk and controls?"),
    ("recs", r"reconciliations?|balance sheet (review|substantiation|reconciliation)"),
    ("accounting", r"accounting (background|knowledge|experience|principles|standards|skills)|accountancy|\baccounting\b"),
    ("diligence", r"due diligence|transaction services|financial diligence|\bfdd\b|quality of earnings"),
    ("excel", r"\bexcel\b"),
)]


def strengths_in(low):
    """{key: matched phrase} for audit-relevant things the advert asks for (in a requirement sentence)."""
    out = {}
    head = _REQ_HEAD.search(low)
    for key, rx in STRENGTHS:
        for m in rx.finditer(low):
            sent = _sentence(low, m.start(), m.end())
            if _STRENGTH_SKIP.search(sent):
                continue
            if (head and m.start() > head.start()) or (not head and _ASKS.search(sent)):
                out[key] = m.group(0).strip()
                break
    return out


def _soft(low, m):
    return bool(_SOFT.search(low[m.end():m.end() + 70]) or _SOFT_BEFORE.search(low[max(0, m.start() - 40):m.start()]))


def eligibility(job, level, profile, stage="now"):
    """Return (reach, flags, pros) for how realistic the role is at this stage ("now" or "aca").
    flags are the gaps; pros are the parts of the advert that play to the candidate's experience."""
    st = stages(profile)[stage]
    aca = st["aca"]
    q = profile["qualifications"]
    text = " ".join([job.get("title", ""), job.get("description", ""), job["extra"].get("course", "")])
    low = text.lower().replace("’", "'")
    flags, pros, m = [], [], 1.0
    us = job.get("_region") == "US"
    you = f"ACA + about {st['years']:g} yrs of audit" if aca else f"your {st['years']:g} yrs of audit"
    base = {"apprentice": .85, "intern": .08, "entry": .62, "mid": .45,
            "senior": .15 if aca else .06, "exec": .02 if aca else .015}[level]
    advert_read = len(job.get("description") or "") >= 300 or level == "apprentice"

    if level == "intern":
        flags.append("Usually for current university students")
    if re.search(r"currently (studying|enrolled)|penultimate year|final[- ]year (student|undergrad)|"
                 r"graduating in 20\d\d|expected graduation|pursuing a (bachelor|degree)|recent graduates?|"
                 r"soon[- ]to[- ]be graduates?|graduated (in|within)|class of 20\d\d|(in|of) your (final|penultimate) year|"
                 r"graduation (year|date) of 20\d\d|job family: undergraduate|graduate (programme|scheme) for (graduates|students)|"
                 r"are you a student|(first|second|1st|2nd)[- ]year (college |university )?students?|high school students?|"
                 r"open to (college|university) students|students? in the (last|final) year of|(last|final) year of (their|your) (bachelor|degree|studies)", low):
        m *= .2
        flags.append("Student / recent-graduate only")

    # Experience
    worst = None
    for n, pqe, ctx, credit in (years_required(low) if advert_read else []):
        have = 0 if pqe else st["years"] * credit
        f = _gap_factor(n - have)
        if f < 1 and (worst is None or f < worst[0]):
            worst = (f, n, pqe, ctx, credit)
    reqs = years_required(low) if advert_read else []
    title = (job.get("title") or "").lower()
    if not reqs and re.search(r"\bsenior\b", title) and level in ("mid", "senior"):
        credit = _credit(title)
        f = _gap_factor(3 - st["years"] * credit)
        if f < 1 and (worst is None or f < worst[0]):
            worst = (f, 3, False, "senior title, no years given", credit)
    need = next((x for x in _NEEDS_EXP.finditer(low) if not _soft(low, x)
                 and not re.search(r"\b(is |are )?(preferred|desirable|a plus|advantageous|an advantage|beneficial)\b", x.group(2))),
                None) if advert_read else None
    if need and _credit(need.group(2)) < 1:
        c = _credit(need.group(2))
        m *= .5 if c == 0 else .75
        flags.append(f"Wants direct experience in {_short(need.group(2))} - "
                     + ("audit doesn't cover this" if c == 0 else "audit only partly covers this"))
    met = [(n, ctx) for n, pqe, ctx, credit in reqs if not pqe and credit == 1 and st["years"] >= n and not _soft_years(ctx)]
    if met and not worst:
        n, ctx = max(met)
        pros.append(f"Asks for {n}+ yrs" + (f" ({_short(ctx, 45)})" if ctx else "") + f" - you have {st['years']:g}")
    if advert_read and level not in ("apprentice", "intern"):
        if _BOOK.search(low):
            m *= .3
            flags.append("Wants an established client book / industry relationships - you'd be building these from scratch")
        if level in ("entry", "mid") and _LEADS.search(low):
            m *= .5
            flags.append("Wants someone who already leads deals or manages people")
    langs = profile.get("languages", ["english"])
    lang = re.search(r"\b(?:fluen\w*|native|proficien\w*|business[- ]level|bilingual|strong)\b[^.•]{0,25}?\b(" + _LANGS + r")\b|"
                     r"\b(" + _LANGS + r")\b[^.•]{0,15}?\b(fluency|fluent|speaker|speaking|language skills|(is )?required|proficiency)\b", low)
    if advert_read and lang and (lang.group(1) or lang.group(2)) not in langs:
        near = low[max(0, lang.start() - 50):lang.end() + 40]  # adverts often lose bullet breaks, so look close by
        if not _SYS_SOFT.search(near):
            m *= .2
            flags.append(f"Needs fluent {(lang.group(1) or lang.group(2)).title()}")
    elif re.search(r"\bbilingual\b", job.get("title", ""), re.I):
        m *= .2
        flags.append("Needs a second language (bilingual role)")
    if worst:
        f, n, pqe, ctx, credit = worst
        m *= f
        what = f"{n}+ years post-qualification" if pqe else f"{n}+ years' experience"
        if ctx == "senior title, no years given":
            what, ctx = "several years' experience (senior role, no years stated)", ""
        if pqe:
            why = "you'd be newly qualified" if aca else "you aren't qualified yet"
        elif credit == 1:
            why = f"you'd have about {st['years']:g}" if aca else f"you have {st['years']:g}"
        else:
            why = "audit doesn't count here" if credit == 0 else "audit only partly counts here"
        flags.append(f"Asks for {what}" + (f" ({_short(ctx)})" if ctx and not pqe else "") + f" - {why}")

    # Coding
    if advert_read and not profile.get("can_code"):
        need_code, lang = coding_need(low)
        if need_code == "hard":
            m *= .35
            flags.append(f"Needs you to write code ({'Python/SQL' if lang in ('python', 'sql') else lang}) - you'd need to learn it first")
        elif need_code == "soft":
            m *= .92
            flags.append("Python/SQL is a plus, not required - worth learning")

    # Degrees
    if not q.get("degree") and level not in ("apprentice", "intern") and advert_read and not _NO_DEGREE.search(low):
        # A master's offered as an alternative to a first degree ("bachelor's or master's") isn't a master's requirement.
        adv = next((x for x in _ADV_DEGREE.finditer(low)
                    if not re.search(r"(bachelor|undergraduate|ba/bsc|bsc|first degree|\bor\b|and/or)[^.]{0,30}$",
                                     low[max(0, x.start() - 45):x.start()])
                    and not re.search(r"^[^.]{0,40}or equivalent", low[x.end():x.end() + 60])), None)
        stem = next((x for x in _STEM_DEGREE.finditer(low)
                     if _STEM.search(x.group(2)) and not _FIN_SUBJ.search(x.group(2))), None)
        deg = next((x for x in _DEGREE.finditer(low)), None)
        if adv and not _soft(low, adv):
            m *= .12
            flags.append("Asks for a master's or PhD")
        elif stem and not _soft(low, stem):
            m *= .3
            flags.append("Asks for a maths / science / engineering-type degree")
        elif deg:
            equiv = re.search(r"degree[^.]{0,60}\bor\b[^.]{0,30}(equivalent|professional qualification|aca|acca|"
                              r"relevant experience|experience)|no degree|degree not required", low)
            # "Bachelor's degree or equivalent: in Finance ... or equivalent degree - Required" means an equivalent *degree*.
            if equiv and re.search(r"equivalent (degree|qualification from)|(degree|diploma)[^.]{0,20}equivalent[^.]{0,80}"
                                   r"\bdegree\b[^.]{0,15}required", low[equiv.start():equiv.end() + 120]):
                equiv = None
            if _soft(low, deg):
                m *= 1 if aca else .85
                flags.append("A degree is preferred, not required")
            elif equiv:
                m *= 1 if aca else .8
                flags.append("Asks for a degree or equivalent" + (" - ACA should count" if aca else " - lean on AAT L4 + experience"))
            else:
                m *= .85 if aca else .5
                flags.append("Asks for a degree" + (" - many firms accept ACA instead, but check" if aca
                                                     else " - you'd need them to accept AAT L4 + experience"))

    # Graduate schemes: normally degree-gated, and they restart a 3-year training contract.
    # (Campus hiring: "Graduate Analyst", "2027 Data & AI Program", "New Grad", "Campus".)
    if level == "entry" and re.search(r"\bgraduates?\b|\bgrad\b|\bcampus\b|\b20\d\d\b[^|]{0,60}\bprogram(me)?\b|\bprogram(me)?\b[^|]{0,40}\b20\d\d\b",
                                      title) and not re.search(r"\bnon[- ]graduate", title):
        if not q.get("degree") and not _NO_DEGREE.search(low) and not any(f.startswith(("Asks for a degree", "A degree is")) for f in flags):
            m *= .5 if aca else .3
            flags.append("Graduate programme - usually needs a degree" + (", though ACA may count" if aca else ""))
        if not aca and re.search(r"\b(aca|acca|cima|icas|chartered accountan)", low):
            m *= .6
            flags.append("Restarts a professional-qualification training contract - a step back from year 3 of yours")

    # Specialist qualifications outside accountancy (RICS, regulated financial planning, CTA/ATT, CFA charter).
    other = next((x for x in _OTHER_QUAL.finditer(low)
                  if not _soft(low, x) and not re.search(r"(\bor\b|towards|studying|working)[^.]{0,25}$", low[max(0, x.start() - 30):x.start()])),
                 None) if advert_read and level not in ("apprentice", "intern") else None
    if other:
        g = other.group(0)
        name = next((n for k, n in _OTHER_QUAL_NAME if k in g), "a specialist qualification")
        m *= .2
        flags.append(f"Needs {name} - AAT/ACA don't cover it")

    # Accountancy qualification
    qual = next((x for x in _QUALIFIED.finditer(low)
                 if not _soft(low, x) and not re.search(r"(\bor\b|become|becoming|towards|on track to)[^.]{0,25}$",
                                                       low[max(0, x.start() - 30):x.start()])), None)
    if level == "apprentice":
        qual = None  # apprenticeships describe the qualification you'll gain, not one you need
    part = _PART_QUALIFIED.search(low) if advert_read else None
    if qual and not part:
        sent = _sentence(low, qual.start(), qual.end())
        cpa_only = re.search(r"\bcpa\b", sent) and not re.search(r"\b(aca|acca|icaew|chartered|ca\b|cima)|or equivalent|"
                                                                r"(cpa|or) (or|/) ?(equivalent|international)", sent)
        if cpa_only:
            m *= .5 if aca else .15
            flags.append("Wants a US CPA - ACA may not be accepted on its own; you'd likely need the CPA too" if aca
                         else "Needs a qualified CPA - not yet")
        elif aca:
            m *= 1.15
            pros.append("Wants a qualified accountant - ACA ticks this")
        else:
            m *= .15
            flags.append("Needs a qualified accountant - you're part-way through ACA, not qualified yet")
    elif part and aca:
        pros.append("Takes part-qualified or qualified accountants - ACA ticks this")
    elif part:
        m *= 1.15
        pros.append(f'Open to part-qualified candidates ("{_short(part.group(0), 30)}") - you are, with AAT L4 and ACA under way')
    elif advert_read and not aca and re.search(r"\baat\b", low) and level != "apprentice":
        pros.append("Mentions AAT - you hold AAT Level 4")
    if advert_read and not qual and re.search(r"accounting qualification|accountancy qualification|qualified accountant", low):
        pros.append("Values an accounting qualification - " + ("ACA ticks this" if aca else "you have AAT L4, ACA under way"))

    # Specialist systems (Oracle, SAP...): the candidate knows Excel and audit tools, not finance platforms.
    if advert_read or re.search(r"\b(oracle|sap)\b", job.get("title", ""), re.I):
        kind, names = systems_need(job.get("title", ""), low, profile.get("systems_known", []))
        if kind:
            m *= _SYS_MULT[kind]
            nm = "/".join(names)
            flags.append({
                "title": f"Specialist {nm} role - needs deep hands-on {nm} experience you don't have",
                "deep": f"Needs deep hands-on {nm} experience - you've worked in Excel and audit tools, not {nm}",
                "req": f"Asks for {nm} experience - you haven't used {nm}",
                "generic": f"Wants finance-system experience (e.g. {nm}) - you've seen clients' systems in audit, not run them",
                "soft": f"{nm} experience is a plus, not required",
            }[kind])

    # What the advert asks for that audit gives you - quoted from the advert, never assumed.
    if advert_read and level != "apprentice":
        found = strengths_in(low)
        core = [found[k] for k in ("audit", "reporting", "controls", "recs", "accounting", "diligence") if k in found]
        if core:
            m *= 1 + .05 * min(len(core), 3)
            pros.append("Asks for " + ", ".join(dict.fromkeys(core[:3])) + f" - core to {you}")
        if "excel" in found:
            pros.append("Excel-heavy - your strongest tool")

    # An experienced hire whose advert asks for nothing audit gives you: you'd be up against people from that field.
    if advert_read and level in ("mid", "senior") and not [x for x in pros if not x.startswith(("Excel-heavy", "Needs US work"))]:
        m *= .75
        flags.append("Nothing in the advert matches audit experience - you'd compete with people already in this field")

    # US roles: no visa issue, but UK qualifications are less familiar there.
    if us:
        if re.search(r"sponsor|work authori[sz]ation|authori[sz]ed to work|eligible to work in the (u\.?s|united states)|"
                     r"u\.s\. (citizen|person)|us citizen", low):
            pros.append("Needs US work rights / no visa sponsorship - your US citizenship covers this")
        if aca:
            m *= .9
            flags.append("US role: ACA is respected, but some US employers prefer a CPA")
        else:
            m *= .8
            flags.append("US role: AAT isn't known in the US - lean on your audit experience")

    if level == "apprentice":
        lvl = re.search(r"level (\d)", low)
        if aca:
            m *= .25
            flags.append("Apprenticeship - you'd already hold a higher qualification")
        elif lvl and int(lvl.group(1)) <= 4:
            m *= .35
            flags.append(f"Level {lvl.group(1)} - at or below a level you already hold")
        elif lvl and int(lvl.group(1)) >= 6:
            m *= .4
            flags.append(f"Level {lvl.group(1)} degree apprenticeship - restarts training on apprentice pay, a step back from year 3 of yours")
        if re.search(r"\baat\b", job.get("title", ""), re.I):
            m *= .5
    # GCSE / UCAS checks
    for g in re.findall(r"(?:grade|at) ([5-9])\b[^.]{0,60}(?:english|maths|mathematics)", low)[:1]:
        if int(g) > min(q["gcse"].get("English Language", 0), q["gcse"].get("Maths", 0)):
            m *= .85
            flags.append(f"GCSE grade {g} asked for - above one of your GCSEs")
    for pts in re.findall(r"(\d{2,3})\s*ucas", low)[:1]:
        if int(pts) > q.get("ucas_points", 0):
            m *= .8
            flags.append(f"{pts} UCAS points asked for - more than you have")
    if re.search(r"\b(a\*?a\*?a|aab|abb|bbb)\b", low) and "a level" in low:
        m *= .85
        flags.append("Asks for higher A-level grades than yours")
    if re.search(r"\bquant|\bstrats?\b|\bstrategist\b", job.get("title", ""), re.I):
        m *= .4
        flags.append("Quant role: usually needs a maths / physics / CS degree")
    # Quant-heavy desks: test-based hiring, very mental-maths heavy.
    if job.get("_category") == "prop_trading" and function_of(job["title"], job.get("department", "")) == "front":
        m *= .5
        flags.append("Stretch: hires on intense maths/probability tests")
    if not advert_read:
        m *= .85
        flags.append("Advert text not available - check the requirements yourself")
    return min(base * m, .95), flags, pros


# Banks state the corporate title in the advert ("Vice President Expectations", "Corporate title: Associate").
_CORP_TITLE = re.compile(r"\b(assistant vice president|vice president|managing director|executive director|director|associate|analyst)"
                         r"\s+expectations\b|corporate title:?\s*(assistant vice president|vice president|managing director|"
                         r"executive director|director|associate|analyst)\b", re.I)
_CORP_LEVEL = {"analyst": "entry", "associate": "mid", "assistant vice president": "mid", "vice president": "senior",
               "executive director": "senior", "director": "exec", "managing director": "exec"}


def level_from_advert(title, desc, dept=""):
    level = level_of(title, dept)
    if level in ("entry", "mid") and not re.search(r"assistant manager|\bavp\b|assistant vice president|\bsenior\b", title or "", re.I):
        c = _CORP_TITLE.search(desc or "")
        # "Corporate title: X" is explicit; Barclays-style "X Expectations" only fills in a title with no level words.
        if c and (c.group(2) or level == "mid"):
            return _CORP_LEVEL[(c.group(1) or c.group(2)).lower()]
    return level


# ---------------------------------------------------------------- main scorer
def score(job, profile, aliases):
    cat = job.get("category") or classify(job.get("company"), aliases)
    job["_category"] = cat
    region = job.get("region") or ("UK" if job.get("source") in ("adzuna", "reed", "gov.uk apprenticeships")
                                   else region_of(job.get("location")) or "UK")
    job["_region"] = region
    level = level_from_advert(job["title"], job.get("description", ""), job.get("department", ""))
    func = function_of(job["title"], job.get("department", ""))
    interest, tracks = interest_of(job["title"], f"{job.get('department', '')} {job.get('description', '')}")

    cur = job.get("salary_currency") or ("USD" if region == "US" else "GBP")
    smin, smax = job.get("salary_min"), job.get("salary_max")
    if not smin and not smax:
        smin, smax = parse_salary(job.get("salary_text") or "", cur)
    if not smin and not smax and job.get("source") not in ("gov.uk apprenticeships",):
        # Only trust salaries spelled out in the first part of the description (US: next to a pay word, anywhere).
        desc = job.get("description") or ""
        smin, smax = parse_salary(find_pay_text(desc, cur) if cur == "USD" else desc[:2500], cur)
    base_pay = None
    if smin or smax:
        base_pay = ((smin or smax) + (smax or smin)) / 2
        if cur == "USD":
            base_pay *= USD_TO_GBP
    # Advertised pay well above entry level means it isn't an entry job, whatever the title says (prop firms excepted).
    if base_pay and cat not in ("prop_trading", "hedge_fund") and level in ("entry", "mid"):
        usd = base_pay / USD_TO_GBP
        if level == "entry" and (usd > 150_000 if region == "US" else base_pay > 90_000):
            level = "mid"
        if level == "mid" and (usd > 260_000 if region == "US" else base_pay > 150_000):
            level = "senior"
    li = LEVELS.index(level)
    london = is_london(job.get("location")) or "remote" in (job.get("location") or "").lower()
    if base_pay:
        bonus = 0 if level in ("apprentice", "intern") else BONUS[cat] * (1 if func == "front" else .4)
        comp = base_pay * (1 + bonus)
        comp_basis = "advertised"
    else:
        comp = BANDS[cat][li] * 1000
        if level not in ("apprentice", "intern"):
            if func == "back":
                comp *= BACK_OFFICE[cat]
            elif func in ("other", "tech") and cat not in ("big4", "mid_tier", "other"):
                comp *= (.55 if cat in ELITE else .75) if func == "other" else .9
        if region == "US":
            comp *= US_PAY["hub" if US_HUBS.search(job.get("location") or "") else "other"]
        elif not london:
            comp *= .85
        comp_basis = "estimated"

    def reach_at(stage):
        reach, flags, pros = eligibility(job, level, profile, stage)
        if cat in ELITE and func == "front":
            if level == "mid":
                reach *= .5
                flags.append("Associate-level: usually hired from 2-3 yrs as an analyst or Big 4 deals")
            elif level == "entry":
                reach *= .6
                flags.append("Front-office analyst roles are fiercely competitive and often degree-screened")
        if "Transaction services" in tracks and cat in ("big4", "mid_tier") and level in ("entry", "mid"):
            reach = min(reach * 1.25, .95)
            pros.append("Audit -> deal advisory (TS, restructuring, forensic) is a well-trodden move")
        return reach, flags, pros

    reach, flags, pros = reach_at("now")
    reach_aca, flags_aca, pros_aca = reach_at("aca")
    wp = workplace_of(job)
    wmult = profile["preferences"]["remote_multiplier"].get(wp, 1.0)
    fit = (.25 + .75 * interest) * wmult
    match, match_aca = comp * reach * fit, comp * reach_aca * fit

    job.update(
        category=cat, category_label=CATEGORY_LABEL[cat], level=level, function=func, region=region,
        interest=round(interest, 2), tracks=tracks, workplace_type=wp,
        summary=role_summary(job.get("description") or "", job.get("title", "")),
        currency=cur, base_pay=round(base_pay) if base_pay else None, comp=round(comp, -2), comp_basis=comp_basis,
        comp_usd=round(comp / USD_TO_GBP, -3) if region == "US" else None,
        reach=round(reach, 2), flags=flags, pros=pros, match=round(match),
        reach_aca=round(reach_aca, 2), flags_aca=flags_aca, pros_aca=pros_aca, match_aca=round(match_aca),
    )
    job.pop("_category", None)
    job.pop("_region", None)
    return job
