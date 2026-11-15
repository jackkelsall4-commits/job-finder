"""Scores each vacancy for: pay, how reachable it is for this candidate now,
fit with the target areas, and work-from-home options.

All pay figures are annual GBP. When an advert shows no salary, pay is
estimated from the employer type and the role's seniority using the bands
below (rough London market levels for 2025-26: base plus typical bonus).
They are deliberately round numbers; edit them if you know better.
"""
import re

from .text import is_london

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
                  r"identity (and|&) access|csirt|\bsoc\b|incident response", re.I)
NON_FIN = re.compile(r"\b(hr|human resources|people partner|recruit\w*|talent|front desk|meteorolog\w*|scientist|social media|team assistant|office assistant|legal|counsel|paralegal|marketing|"
                     r"communications|customer experience|branch|cashier|contact centre|account executive|reporter|journalist|editor|lawyer|solicitor|attorney|designer|office manager|receptionist|facilities|executive assistant|"
                     r"personal assistant|administrator|chef|cleaner|driver|nurse|teacher|customer service|"
                     r"call centre|retail assistant|barista|warehouse)\b", re.I)


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
                 r"off[- ]cycle|industrial|work experience|year in industry", t):
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
    if re.search(r"graduates?|\bgrads?\b|junior|trainee|entry|new grad|campus|\banalyst\b(?!.*senior)|\bassistant\b|\bexecutive\b", t) \
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
_MONEY = re.compile(r"£\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(k\b)?", re.I)


def parse_salary(text):
    """Return (min, max) annual GBP from text such as '£45,000 - £55,000 a year' or '£500 a week'."""
    if not text:
        return None, None
    vals = []
    for m in list(_MONEY.finditer(text))[:3]:
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
_NEEDS_EXP = re.compile(r"(you have|you'll have|you will have|you'll need|you need|you must have|must have|required|requirements|"
                        r"essential|skills and experience|what you need|what you'll bring|you bring|you'll bring|ideal candidate)"
                        r"\W{0,4}(?:significant |proven |strong |solid |demonstrable |relevant |prior |previous |direct |hands-on )*"
                        r"experience (?:in|of|with|within|across)\s+([^.;]{0,90})", re.I)


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
    return 1


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
_DEGREE = re.compile(r"(bachelor'?s?|undergraduate|university|third[- ]level|3rd[- ]level|college) degree|degree[- ](level|educated)|"
                     r"educated to degree|tertiary (qualification|degree|education)|\b2[:.]1\b|degree (is )?(required|essential)|hold a degree|have a degree|"
                     r"(a|relevant|strong) degree in|\bbsc\b|\bb\.?a\.? in\b|\bbs\b (in|degree)|degree in (finance|economics|"
                     r"accounting|business|math)", re.I)
_STEM_DEGREE = re.compile(r"degree[^.]{0,30}?\b(in|of)\b([^.]{0,90})", re.I)
_STEM = re.compile(r"meteorolog|atmospheric|physics|mathemat|\bmaths\b|computer science|engineering|statistics|chemistry|"
                   r"biology|\blaw\b|medicine|data science|machine learning|actuarial science", re.I)
_FIN_SUBJ = re.compile(r"financ|account|econom|business", re.I)
_QUALIFIED = re.compile(r"newly[- ]qualified|\bnq\b|fully[- ]qualified|qualified (aca|acca|cima|cpa|accountant|chartered)|"
                        r"\b(aca|acca|cima|cpa|icaew)\b[^.]{0,30}?\bqualified\b|chartered accountan(t|cy)|post[- ]qualification|"
                        r"\bpqe\b|qualified accountant|(accountancy|accounting) qualification( or equivalent)?( is)? (required|essential)|"
                        r"(aca|acca|cima|cpa)( or (aca|acca|cima|cpa|equivalent))*,? (is )?(required|essential)|"
                        r"must be (aca|acca|a qualified|a chartered)|professionally qualified", re.I)
_PART_QUALIFIED = re.compile(r"part[- ]qualified|studying (towards|for)|working towards|qualified or (part|studying|nearly)|"
                             r"\bqbe\b|qualified by experience|nearly qualified|finalist", re.I)


def _soft(low, m):
    return bool(_SOFT.search(low[m.end():m.end() + 70]) or _SOFT_BEFORE.search(low[max(0, m.start() - 40):m.start()]))


def eligibility(job, level, profile, stage="now"):
    """Return (multiplier, flags) for how realistic the role is at this stage ("now" or "aca")."""
    st = stages(profile)[stage]
    aca = st["aca"]
    q = profile["qualifications"]
    text = " ".join([job.get("title", ""), job.get("description", ""), job["extra"].get("course", "")])
    low = text.lower().replace("’", "'")
    flags, m = [], 1.0
    base = {"apprentice": .85, "intern": .08, "entry": .62, "mid": .45,
            "senior": .15 if aca else .06, "exec": .02 if aca else .015}[level]
    advert_read = len(job.get("description") or "") >= 300 or level == "apprentice"

    if level == "intern":
        flags.append("Usually for current university students")
    if re.search(r"currently (studying|enrolled)|penultimate year|final[- ]year (student|undergrad)|"
                 r"graduating in 20\d\d|expected graduation|pursuing a (bachelor|degree)|recent graduates?|"
                 r"soon[- ]to[- ]be graduates?|graduated (in|within)|class of 20\d\d|(in|of) your (final|penultimate) year|"
                 r"graduation (year|date) of 20\d\d|job family: undergraduate|graduate (programme|scheme) for (graduates|students)", low):
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
    need = next((x for x in _NEEDS_EXP.finditer(low) if not _soft(low, x)), None) if advert_read else None
    if need and _credit(need.group(2)) < 1:
        c = _credit(need.group(2))
        m *= .5 if c == 0 else .75
        flags.append(f"Wants direct experience in {_short(need.group(2))} - "
                     + ("audit doesn't cover this" if c == 0 else "audit only partly covers this"))
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

    # Degrees
    if not q.get("degree") and level not in ("apprentice", "intern") and advert_read:
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

    # Accountancy qualification
    qual = next((x for x in _QUALIFIED.finditer(low)
                 if not _soft(low, x) and not re.search(r"(\bor\b|become|becoming|towards|on track to)[^.]{0,25}$",
                                                       low[max(0, x.start() - 30):x.start()])), None)
    if level == "apprentice":
        qual = None  # apprenticeships describe the qualification you'll gain, not one you need
    part = _PART_QUALIFIED.search(low)
    background = re.search(r"\b(aca|acca|aat|icaew|cima)\b|\bbig ?(4|four)\b|audit (background|experience)|"
                           r"accounting qualification", low)
    if qual and not part:
        if aca:
            m *= 1.15
            flags.append("Wants a qualified accountant - ACA ticks this")
        else:
            m *= .15
            flags.append("Needs a qualified accountant (ACA/ACCA) - not yet")
    elif part or background:
        m *= 1.2
        flags.append("Asks for audit / ACA / AAT background - plays to your experience")

    if level == "apprentice":
        lvl = re.search(r"level (\d)", low)
        if aca:
            m *= .25
            flags.append("Apprenticeship - you'd already hold a higher qualification")
        elif lvl and int(lvl.group(1)) <= 4:
            m *= .35
            flags.append(f"Level {lvl.group(1)} - at or below a level you already hold")
        elif lvl and int(lvl.group(1)) >= 6:
            flags.append(f"Level {lvl.group(1)} degree apprenticeship")
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
    return min(base * m, .95), flags


# ---------------------------------------------------------------- main scorer
def score(job, profile, aliases):
    cat = job.get("category") or classify(job.get("company"), aliases)
    job["_category"] = cat
    level = level_of(job["title"], job.get("department", ""))
    func = function_of(job["title"], job.get("department", ""))
    interest, tracks = interest_of(job["title"], f"{job.get('department', '')} {job.get('description', '')}")

    smin, smax = job.get("salary_min"), job.get("salary_max")
    if not smin and not smax:
        smin, smax = parse_salary(job.get("salary_text") or "")
    if not smin and not smax and job.get("source") not in ("gov.uk apprenticeships",):
        # Only trust salaries spelled out in the first part of the description.
        smin, smax = parse_salary((job.get("description") or "")[:2500])
    base_pay = None
    if smin or smax:
        base_pay = ((smin or smax) + (smax or smin)) / 2
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
        if not london:
            comp *= .85
        comp_basis = "estimated"

    def reach_at(stage):
        reach, flags = eligibility(job, level, profile, stage)
        if cat in ELITE and func == "front":
            if level == "mid":
                reach *= .5
                flags.append("Associate-level: usually hired from 2-3 yrs as an analyst or Big 4 deals")
            elif level == "entry":
                reach *= .6
                flags.append("Front-office analyst roles are fiercely competitive and often degree-screened")
        if "Transaction services" in tracks and cat in ("big4", "mid_tier") and level in ("entry", "mid"):
            reach = min(reach * 1.25, .95)
            flags.append("Audit -> TS is the classic Big 4 move")
        return reach, flags

    reach, flags = reach_at("now")
    reach_aca, flags_aca = reach_at("aca")
    wp = workplace_of(job)
    wmult = profile["preferences"]["remote_multiplier"].get(wp, 1.0)
    fit = (.25 + .75 * interest) * wmult
    match, match_aca = comp * reach * fit, comp * reach_aca * fit

    job.update(
        category=cat, category_label=CATEGORY_LABEL[cat], level=level, function=func,
        interest=round(interest, 2), tracks=tracks, workplace_type=wp,
        base_pay=round(base_pay) if base_pay else None, comp=round(comp, -2), comp_basis=comp_basis,
        reach=round(reach, 2), flags=flags, match=round(match),
        reach_aca=round(reach_aca, 2), flags_aca=flags_aca, match_aca=round(match_aca),
    )
    job.pop("_category", None)
    return job
