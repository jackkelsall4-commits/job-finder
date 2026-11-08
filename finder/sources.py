"""Adapters that pull live vacancies and normalise them into one shape.

Every adapter returns a list of dicts with these keys:
  source, company, category, title, location, url, posted, department,
  description, salary_min, salary_max, salary_text, workplace, extra
Keyless sources run every time; Adzuna, Reed and the GOV.UK apprenticeship API
switch on when their keys are present in the environment.
"""
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .http import fetch, fetch_json
from .text import html_to_text, is_uk
from . import scoring

# Titles worth fetching full descriptions for (Workday/Oracle list views have no body text).
RELEVANT_TITLE = re.compile(
    r"trad|market|invest|portfolio|equity|credit|m&a|merger|acquisition|corporate finance|"
    r"banking|banker|capital|deal|transaction|due diligence|valuation|restructur|private|"
    r"fund|financ|account|audit|control|analyst|associate|apprentic|graduate|trainee|"
    r"research|advisory|leveraged|ecm|dcm|treasury|fp&a|structur|sales",
    re.I,
)


def job(**kw):
    base = dict(source="", company="", category=None, title="", location="", url="", posted="",
                department="", description="", salary_min=None, salary_max=None, salary_text="",
                workplace=None, extra={})
    base.update(kw)
    return base


# ---------------------------------------------------------------- Greenhouse
def greenhouse(firm):
    d = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{firm['slug']}/jobs?content=true")
    out = []
    for j in d.get("jobs", []):
        offices = " / ".join(o.get("name", "") + " " + (o.get("location") or "") for o in j.get("offices", []))
        loc = (j.get("location") or {}).get("name", "")
        if not is_uk(loc, offices):
            continue
        if not is_uk(loc):  # multi-office posting: show the UK office
            uk = [o.get("name", "") for o in j.get("offices", []) if is_uk(o.get("name", ""), o.get("location") or "")]
            loc = " / ".join(uk) + (f" (also {loc})" if loc else "")
        out.append(job(
            source="greenhouse", company=firm["name"], category=firm["category"], title=j["title"],
            location=loc, url=j.get("absolute_url", ""), posted=(j.get("updated_at") or "")[:10],
            department=", ".join(x["name"] for x in j.get("departments", [])),
            description=html_to_text(j.get("content", "")),
        ))
    return out


# ---------------------------------------------------------------- Lever
def lever(firm):
    d = fetch_json(f"https://api.lever.co/v0/postings/{firm['slug']}?mode=json")
    out = []
    for j in d:
        c = j.get("categories", {})
        locs = " / ".join([c.get("location") or ""] + (c.get("allLocations") or []))
        if not is_uk(locs, j.get("country")):
            continue
        sr = j.get("salaryRange") or {}
        out.append(job(
            source="lever", company=firm["name"], category=firm["category"], title=j["text"],
            location=locs, url=j.get("hostedUrl", ""),
            posted=time.strftime("%Y-%m-%d", time.gmtime(j.get("createdAt", 0) / 1000)),
            department=" / ".join(filter(None, [c.get("department"), c.get("team")])),
            description=(j.get("descriptionPlain") or "")[:6000],
            salary_min=sr.get("min") if sr.get("currency") == "GBP" else None,
            salary_max=sr.get("max") if sr.get("currency") == "GBP" else None,
            workplace=j.get("workplaceType"),
        ))
    return out


# ---------------------------------------------------------------- Ashby
def ashby(firm):
    d = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{firm['slug']}?includeCompensation=true")
    out = []
    for j in d.get("jobs", []):
        locs = " / ".join([j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations", [])])
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or ""
        if not is_uk(locs, country):
            continue
        smin = smax = None
        for comp in ((j.get("compensation") or {}).get("summaryComponents") or []):
            if comp.get("compensationType") == "Salary" and comp.get("currencyCode") == "GBP":
                smin, smax = comp.get("minValue"), comp.get("maxValue")
        wp = "remote" if j.get("isRemote") else (j.get("workplaceType") or None)
        out.append(job(
            source="ashby", company=firm["name"], category=firm["category"], title=j["title"],
            location=locs, url=j.get("jobUrl", ""), posted=(j.get("publishedAt") or "")[:10],
            department=" / ".join(filter(None, [j.get("department"), j.get("team")])),
            description=(j.get("descriptionPlain") or "")[:6000],
            salary_min=smin, salary_max=smax,
            salary_text=(j.get("compensation") or {}).get("compensationTierSummary") or "",
            workplace=wp,
        ))
    return out


# ---------------------------------------------------------------- SmartRecruiters
def smartrecruiters(firm):
    base = f"https://api.smartrecruiters.com/v1/companies/{firm['slug']}/postings"
    rows, offset = [], 0
    while offset < 500:
        d = fetch_json(f"{base}?limit=100&offset={offset}&country=gb")
        rows += d.get("content", [])
        offset += 100
        if offset >= d.get("totalFound", 0):
            break

    def detail(r):
        desc = ""
        try:
            sec = fetch_json(f"{base}/{r['id']}", retries=1).get("jobAd", {}).get("sections", {})
            desc = " ".join(html_to_text(sec.get(k, {}).get("text", "")) for k in ("jobDescription", "qualifications", "additionalInformation"))
        except Exception:
            pass
        loc = r.get("location", {})
        wp = "remote" if loc.get("remote") else "hybrid" if loc.get("hybrid") else None
        return job(
            source="smartrecruiters", company=firm["name"], category=firm["category"], title=r["name"],
            location=loc.get("fullLocation", ""), url=f"https://jobs.smartrecruiters.com/{firm['slug']}/{r['id']}",
            posted=(r.get("releasedDate") or "")[:10], department=(r.get("department") or {}).get("label", ""),
            description=desc[:6000], workplace=wp,
        )

    rows = [r for r in rows if is_uk(r.get("location", {}).get("fullLocation", ""), r.get("location", {}).get("country", ""))]
    with ThreadPoolExecutor(6) as ex:
        return list(ex.map(detail, rows[:80]))


# ---------------------------------------------------------------- SuccessFactors career sites (EY)
def successfactors(firm, max_details=60):
    seen, rows = set(), []
    for q in firm["queries"]:
        for start in range(0, 100, 25):
            h = fetch(f"https://{firm['host']}{firm['path']}/search/?q={urllib.parse.quote(q)}"
                      f"&locationsearch={urllib.parse.quote(firm.get('location', 'United Kingdom'))}&startrow={start}")
            found = 0
            for href, title, loc in re.findall(
                    r'<tr class="data-row">.*?href="([^"]+)" class="jobTitle-link">(.*?)</a>.*?class="jobLocation">\s*(.*?)\s*</span>',
                    h, re.S):
                found += 1
                if href in seen:
                    continue
                seen.add(href)
                rows.append((href, html_to_text(title), html_to_text(loc)))
            if found < 25:
                break
    rows = [r for r in rows if is_uk(r[2]) and RELEVANT_TITLE.search(r[1])]

    def detail(r):
        href, title, loc = r
        url = f"https://{firm['host']}{href}"
        desc = ""
        try:
            m = re.search(r'class="jobdescription"[^>]*>(.*?)</span>\s*</div>', fetch(url, retries=1), re.S)
            desc = html_to_text(m.group(1)) if m else ""
        except Exception:
            pass
        return job(source="successfactors", company=firm["name"], category=firm["category"], title=title,
                   location=loc.replace(", GB", ", United Kingdom"), url=url, description=desc)

    rows.sort(key=lambda r: -scoring.interest_of(r[1], "")[0])
    with ThreadPoolExecutor(6) as ex:
        return list(ex.map(detail, rows[:max_details]))


# ---------------------------------------------------------------- Workday
def _find_uk_facet(facets):
    """Workday facet filter for UK jobs: the country facet if the site has one,
    otherwise every location value that looks British."""
    country, places = None, {}

    def walk(fs):
        nonlocal country
        for f in fs or []:
            param = f.get("facetParameter", "")
            for v in f.get("values", []) or []:
                if "facetParameter" in v:
                    walk([v])
                elif "ountry" in param and v.get("descriptor") in ("United Kingdom", "UK", "Great Britain"):
                    country = (param, [v["id"]])
                elif "ocation" in param and is_uk(v.get("descriptor", "")):
                    places.setdefault(param, []).append(v["id"])
    walk(facets)
    if country:
        return country
    if places:
        param = max(places, key=lambda k: len(places[k]))
        return param, places[param]
    return None


def workday(firm, max_details=80):
    base = f"https://{firm['host']}/wday/cxs/{firm['tenant']}/{firm['site']}"
    first = fetch_json(base + "/jobs", data={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})
    facet = _find_uk_facet(first.get("facets"))
    applied = {facet[0]: facet[1]} if facet else {}
    search = "" if facet else "United Kingdom"
    postings, offset, total = [], 0, None
    while offset < 600:
        page = fetch_json(base + "/jobs", data={"appliedFacets": applied, "limit": 20, "offset": offset, "searchText": search})
        total = page.get("total", 0) if total is None else total
        batch = page.get("jobPostings", [])
        postings += batch
        offset += 20
        if not batch or offset >= total:
            break
    keep = [p for p in postings if RELEVANT_TITLE.search(p.get("title", ""))]
    keep.sort(key=lambda p: -scoring.interest_of(p.get("title", ""), "")[0])
    site_root = f"https://{firm['host']}/en-US/{firm['site']}"

    def detail(p):
        info = {}
        try:
            info = fetch_json(base + p["externalPath"], retries=1).get("jobPostingInfo", {})
        except Exception:
            pass
        loc = info.get("location") or p.get("locationsText", "")
        country = ((info.get("country") or {}).get("descriptor")) or ""
        if not facet and not is_uk(loc, country):
            return None
        return job(
            source="workday", company=firm["name"], category=firm["category"], title=p["title"],
            location=loc + (f" ({country})" if country and country not in loc else ""),
            url=info.get("externalUrl") or site_root + p["externalPath"],
            posted=info.get("startDate") or p.get("postedOn", ""),
            description=html_to_text(info.get("jobDescription", "")),
            extra={"closes": info.get("endDate")} if info.get("endDate") else {},
        )

    with ThreadPoolExecutor(8) as ex:
        return [j for j in ex.map(detail, keep[:max_details]) if j]


# ---------------------------------------------------------------- Oracle (J.P. Morgan)
def oracle(firm, per_keyword=300):
    seen, rows = set(), []
    for kw in firm.get("keywords", ["United Kingdom"]):
        offset = 0
        while offset < per_keyword:
            q = (f"https://{firm['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
                 f"&expand=requisitionList&finder=findReqs;siteNumber={firm['site']},limit=100,offset={offset},"
                 f"keyword={urllib.parse.quote(kw)},sortBy=POSTING_DATES_DESC")
            items = fetch_json(q)["items"]
            reqs = items[0].get("requisitionList", []) if items else []
            for r in reqs:
                if r["Id"] not in seen and r.get("PrimaryLocationCountry") == "GB":
                    seen.add(r["Id"])
                    rows.append(r)
            offset += 100
            if len(reqs) < 100:
                break
    out = []
    for r in rows:
        if not RELEVANT_TITLE.search(r.get("Title", "")):
            continue
        out.append(job(
            source="oracle", company=firm["name"], category=firm["category"], title=r["Title"],
            location=r.get("PrimaryLocation", ""),
            url=f"https://{firm['host']}/hcmUI/CandidateExperience/en/sites/{firm['site']}/job/{r['Id']}",
            posted=r.get("PostedDate", ""),
            department=" / ".join(filter(None, [r.get("JobFamily"), r.get("JobFunction")])),
            description=r.get("ShortDescriptionStr") or "",
            workplace=(r.get("WorkplaceTypeCode") or "").lower() or None,
        ))
    return out


# ---------------------------------------------------------------- GOV.UK Find an apprenticeship
FAA = "https://www.findapprenticeship.service.gov.uk"
_CARD = re.compile(r'<li class="das-search-results__list-item[^"]*"[^>]*>(.*?)</li>', re.S)


def _p_texts(block):
    return [html_to_text(m) for m in re.findall(r"<p[^>]*>(.*?)</p>", block, re.S)]


def find_an_apprenticeship(terms, pages=4):
    """Scrapes the public search pages (no key needed), highest wage first."""
    out, seen = [], set()
    for term in terms:
        for page in range(1, pages + 1):
            url = (f"{FAA}/apprenticeships?searchTerm={urllib.parse.quote(term)}&sort=SalaryDesc"
                   f"&IncludeCompetitiveSalaryVacancies=true&pageNumber={page}")
            try:
                h = fetch(url)
            except Exception:
                break
            cards = 0
            for block in _CARD.findall(h):
                m = re.search(r'href="(/apprenticeship/(VAC\d+))"', block)
                if not m:
                    continue
                cards += 1
                if m.group(2) in seen:
                    continue
                seen.add(m.group(2))
                title = html_to_text(re.search(r'-vacancy-title">(.*?)</span>', block, re.S).group(1))
                ps = _p_texts(block)
                fields = {}
                for p in ps:
                    for key in ("Start date", "Training course", "Wage"):
                        if p.startswith(key):
                            fields[key] = p[len(key):].strip()
                employer = ps[0] if ps else ""
                location = ps[1] if len(ps) > 1 and not ps[1].startswith(("Start date", "Training")) else ""
                closes = next((p for p in ps if p.startswith("Closes")), "")
                posted = next((p.replace("Posted", "").strip() for p in ps if p.startswith("Posted")), "")
                out.append(job(
                    source="gov.uk apprenticeships", company=employer, title=title, location=location or "England",
                    url=FAA + m.group(1), posted=posted, salary_text=fields.get("Wage", ""),
                    description=f"Apprenticeship. Training course: {fields.get('Training course', '')}.",
                    department="Apprenticeship",
                    extra={"course": fields.get("Training course", ""), "closes": closes, "start": fields.get("Start date", "")},
                ))
            if cards < 10:
                break
            time.sleep(0.4)  # be polite to GOV.UK
    return out


# ---------------------------------------------------------------- Adzuna (free key)
def adzuna(queries, pages=2):
    app_id, key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and key):
        return None
    out = []
    for q in queries:
        for page in range(1, pages + 1):
            d = fetch_json(f"https://api.adzuna.com/v1/api/jobs/gb/search/{page}?app_id={app_id}&app_key={key}"
                           f"&results_per_page=50&what={urllib.parse.quote(q)}&max_days_old=45&sort_by=salary"
                           f"&content-type=application/json")
            for r in d.get("results", []):
                predicted = str(r.get("salary_is_predicted")) == "1"
                out.append(job(
                    source="adzuna", company=(r.get("company") or {}).get("display_name", ""), title=r.get("title", ""),
                    location=(r.get("location") or {}).get("display_name", ""), url=r.get("redirect_url", ""),
                    posted=(r.get("created") or "")[:10], description=html_to_text(r.get("description", "")),
                    salary_min=None if predicted else r.get("salary_min"),
                    salary_max=None if predicted else r.get("salary_max"),
                ))
            if len(d.get("results", [])) < 50:
                break
    return out


# ---------------------------------------------------------------- Reed (free key)
def reed(queries, take=100):
    key = os.environ.get("REED_API_KEY")
    if not key:
        return None
    out = []
    for q in queries:
        d = fetch_json(f"https://www.reed.co.uk/api/1.0/search?keywords={urllib.parse.quote(q)}&resultsToTake={take}",
                       auth=(key, ""))
        for r in d.get("results", []):
            out.append(job(
                source="reed", company=r.get("employerName", ""), title=r.get("jobTitle", ""),
                location=r.get("locationName", ""), url=r.get("jobUrl", ""), posted=r.get("date", ""),
                description=html_to_text(r.get("jobDescription", "")),
                salary_min=r.get("minimumSalary"), salary_max=r.get("maximumSalary"),
                extra={"closes": r.get("expirationDate")},
            ))
    return out


# ---------------------------------------------------------------- GOV.UK apprenticeship API (free key)
def faa_api(terms):
    key = os.environ.get("FAA_API_KEY")
    if not key:
        return None
    out = []
    hdr = {"Ocp-Apim-Subscription-Key": key, "X-Version": "2"}
    for page in range(1, 6):
        d = fetch_json(f"https://api.apprenticeships.education.gov.uk/vacancies/vacancy?PageNumber={page}&PageSize=100"
                       f"&Sort=AgeDesc&FilterBySubscription=false&Routes=Legal%2C%20finance%20and%20accounting", headers=hdr)
        for v in d.get("vacancies", []):
            addr = (v.get("addresses") or [{}])[0]
            w = v.get("wage") or {}
            out.append(job(
                source="gov.uk apprenticeships", company=v.get("employerName", ""), title=v.get("title", ""),
                location=", ".join(filter(None, [addr.get("addressLine3") or addr.get("addressLine4"), addr.get("postcode")])),
                url=v.get("vacancyUrl", ""), posted=(v.get("postedDate") or "")[:10],
                description=v.get("description", ""), salary_text=w.get("wageAdditionalInformation") or w.get("wageType", ""),
                salary_min=w.get("wageAmount") or w.get("wageAmountLowerBound"),
                salary_max=w.get("wageAmount") or w.get("wageAmountUpperBound"),
                department="Apprenticeship",
                extra={"course": (v.get("course") or {}).get("title", ""), "closes": (v.get("closingDate") or "")[:10]},
            ))
        if len(d.get("vacancies", [])) < 100:
            break
    return out


# ================================================================ Firms without a standard ATS feed
# Added Nov 2026 to cover KPMG, Deloitte, BDO, Grant Thornton/RSM (Workday above), Goldman Sachs,
# Evercore, Jefferies, Lazard (Oracle above), HSBC and Interpath. Each reads a public page or
# feed the firm's own careers site uses; nothing needs a login or key.

def _relevant_first(rows, title_of, limit):
    rows = [r for r in rows if RELEVANT_TITLE.search(title_of(r))]
    rows.sort(key=lambda r: -scoring.interest_of(title_of(r), "")[0])
    return rows[:limit]


# ---------------------------------------------------------------- Workable (Interpath)
def workable(firm):
    d = fetch_json(f"https://apply.workable.com/api/v3/accounts/{firm['slug']}/jobs",
                   data={"query": "", "location": [], "department": [], "worktype": [], "remote": []})
    rows, token = list(d.get("results", [])), d.get("nextPage")
    while token and len(rows) < 600:
        d = fetch_json(f"https://apply.workable.com/api/v3/accounts/{firm['slug']}/jobs",
                       data={"query": "", "location": [], "department": [], "worktype": [], "remote": [], "token": token})
        rows += d.get("results", [])
        token = d.get("nextPage")
    out = []
    for j in rows:
        locs = [j.get("location") or {}] + (j.get("locations") or [])
        place = " / ".join(dict.fromkeys(", ".join(filter(None, [l.get("city"), l.get("country")])) for l in locs if l))
        if not (any(l.get("countryCode") == "GB" for l in locs) or is_uk(place)):
            continue
        wp = "remote" if j.get("remote") else (j.get("workplace") or None)
        out.append(job(
            source="workable", company=firm["name"], category=firm["category"], title=j.get("title", ""),
            location=place, url=f"https://apply.workable.com/{firm['slug']}/j/{j.get('shortcode', '')}/",
            posted=(j.get("published") or "")[:10], department=", ".join(j.get("department") or []),
            workplace=wp,
        ))
    return out


# ---------------------------------------------------------------- Eightfold (HSBC)
def eightfold(firm, max_rows=400):
    out, seen = [], set()
    for q in firm.get("queries", [""]):
        start = 0
        while start < max_rows:
            d = fetch_json(f"https://{firm['host']}/api/apply/v2/jobs?domain={firm['domain']}"
                           f"&location=United%20Kingdom&num=50&start={start}&query={urllib.parse.quote(q)}")
            batch = d.get("positions", [])
            for p in batch:
                if p["id"] in seen or not is_uk(p.get("location", ""), " ".join(p.get("locations") or [])):
                    continue
                seen.add(p["id"])
                out.append(job(
                    source="eightfold", company=firm["name"], category=firm["category"], title=p.get("name", ""),
                    location=p.get("location", ""), url=p.get("canonicalPositionUrl") or f"https://{firm['host']}/careers?pid={p['id']}",
                    posted=time.strftime("%Y-%m-%d", time.gmtime(p["t_create"])) if p.get("t_create") else "",
                    department=p.get("department", ""), description=html_to_text(p.get("job_description", "")),
                    workplace=(p.get("work_location_option") or "").lower() or None,
                ))
            start += 50
            if len(batch) < 50 or start >= d.get("count", 0):
                break
    return out


# ---------------------------------------------------------------- Oleeo / tal.net (Evercore, Jefferies)
def oleeo(firm, max_details=40):
    entries = []
    for feed in firm["feeds"]:
        x = fetch(feed)
        for e in re.findall(r"<entry.*?</entry>", x, re.S):
            link = re.search(r'<link rel="alternate" href="([^"?]+)', e)
            title = re.search(r"<title>(.*?)</title>", e, re.S)
            if link and title:
                entries.append((html_to_text(title.group(1)), link.group(1),
                                (re.search(r"<published>(.{10})", e) or [None, ""])[1]))
    entries = list({u: (t, u, p) for t, u, p in entries}.values())

    def detail(row):
        title, url, posted = row
        fields = {}
        try:
            h = fetch(url, retries=1)
            for lab, val in re.findall(r'hform_lbl_text"\s*>(.*?)</span>.*?class="col-sm-9 form_value[^"]*"\s*>(.*?)</div>\s*</div>', h, re.S):
                fields[html_to_text(lab).lower()] = html_to_text(val)
        except Exception:
            pass
        loc = fields.get("location") or fields.get("city") or ""
        if not is_uk(loc, title):
            return None
        return job(source="oleeo", company=firm["name"], category=firm["category"], title=title,
                   location=loc or "London", url=url, posted=posted,
                   department=fields.get("group") or fields.get("division") or "",
                   description=fields.get("job description") or fields.get("description") or "")

    # Feeds are global; only open the ones whose title doesn't name a non-UK city.
    foreign = re.compile(r"new york|houston|san francisco|chicago|boston|los angeles|dallas|atlanta|minneapolis|"
                         r"menlo park|washington|toronto|paris|frankfurt|madrid|milan|munich|tokyo|hong kong|"
                         r"singapore|sydney|dubai|mumbai|são paulo|sao paulo|mexico|zurich|stockholm|amsterdam|tel aviv|"
                         r"\b(ny|ca|tx|il|ma|mn|ga)\b", re.I)
    rows = [r for r in entries if not foreign.search(r[0])]
    rows = _relevant_first(rows, lambda r: r[0], max_details)
    with ThreadPoolExecutor(6) as ex:
        return [j for j in ex.map(detail, rows) if j]


# ---------------------------------------------------------------- KPMG UK (own site, server-rendered)
def kpmg(firm, max_pages=40):
    base = "https://www.kpmgcareers.co.uk"
    out, page, last = [], 1, 1
    while page <= min(last, max_pages):
        h = fetch(f"{base}/search/vacancies/?intakeType={firm.get('intake', 'Experienced')}&page={page}")
        pages = [int(p) for p in re.findall(r'data-page="(\d+)"', h)]
        last = max(pages) if pages else 1
        for vid, block in re.findall(r'<div class="vacancy-description[^"]*" data-vacancy-id="(\d+)"[^>]*>(.*?)(?=<div class="vacancy-description[ "]|vacancy-search-pagination)', h, re.S):
            title = html_to_text((re.search(r"<h3>(.*?)</h3>", block, re.S) or [None, ""])[1])
            fields = {html_to_text(k).rstrip(":").lower(): html_to_text(v)
                      for k, v in re.findall(r'class="vacancy-details-fields"><b>(.*?)</b>(.*?)</p>', block, re.S)}
            link = re.search(r'href="(/Vacancies/[^"]+)"', block)
            desc = html_to_text((re.search(r'class="vacancy-description-text">(.*?)</div>', block, re.S) or [None, ""])[1])
            loc = fields.get("location", "")
            out.append(job(
                source="kpmg", company=firm["name"], category=firm["category"], title=title,
                location=loc if is_uk(loc) else f"{loc}, United Kingdom".lstrip(", "),
                url=base + link.group(1) if link else f"{base}/search/vacancies/",
                department=" / ".join(filter(None, [fields.get("capability"), fields.get("business area")])),
                description=" ".join(filter(None, [fields.get("experience level"), desc])),
                extra={"level": fields.get("experience level", "")},
            ))
        page += 1
        time.sleep(0.3)
    return out


# ---------------------------------------------------------------- Radancy / TMP career sites (BDO)
def radancy(firm, max_details=40):
    base = f"https://{firm['host']}"
    rows, page, pages = [], 1, 1
    while page <= min(pages, 10):
        d = fetch_json(f"{base}/search-jobs/results?ActiveFacetID=0&CurrentPage={page}&RecordsPerPage=100&Distance=50"
                       f"&RadiusUnitType=0&Keywords=&Location=&ShowRadius=False&IsPagination=False&FacetTerm=&FacetType=0"
                       f"&SearchResultsModuleName=Search+Results&SearchFiltersModuleName=Search+Filters&SortCriteria=0"
                       f"&SortDirection=0&SearchType=5&ResultsType=0", headers={"X-Requested-With": "XMLHttpRequest"})
        r = d.get("results", "")
        pages = int((re.search(r'data-total-pages="(\d+)"', r) or [None, 1])[1])
        for href, title, loc in re.findall(r'<a href="(/job/[^"]+)"[^>]*>\s*<h3[^>]*>(.*?)</h3>.*?class="job-location[^"]*">(.*?)</span>', r, re.S):
            rows.append((base + href, html_to_text(title), html_to_text(loc)))
        page += 1
    rows = [r for r in dict.fromkeys(rows) if is_uk(r[2]) or "united kingdom" not in r[2].lower()]

    def detail(r):
        url, title, loc = r
        desc = ""
        try:
            h = fetch(url, retries=0, timeout=20)
            m = re.search(r'class="ats-description[^"]*"[^>]*>(.*?)</div>\s*(?:</div>|<div class="job-)', h, re.S) or \
                re.search(r'<div class="job-description[^"]*"[^>]*>(.*?)</section>', h, re.S)
            desc = html_to_text(m.group(1)) if m else ""
        except Exception:
            pass
        return job(source="radancy", company=firm["name"], category=firm["category"], title=title,
                   location=loc, url=url, description=desc)

    rows = _relevant_first(rows, lambda r: r[1], max_details)
    with ThreadPoolExecutor(3) as ex:  # job pages are ~550 KB and the site slows down under load
        return list(ex.map(detail, rows))


# ---------------------------------------------------------------- Avature portals (Deloitte UK)
def avature(firm, max_details=60):
    """Deloitte's server refuses some older TLS stacks (e.g. macOS LibreSSL); it works from OpenSSL 3."""
    base = f"https://{firm['host']}/{firm['portal']}"
    rows = {}
    for q in firm["queries"]:
        for offset in range(0, 200, 20):
            h = fetch(f"{base}/SearchJobs/?search={urllib.parse.quote(q)}&jobOffset={offset}")
            found = re.findall(r'<h3 class="article__header__text__title[^"]*">\s*<a href="([^"]+/JobDetail/[^"]+)">\s*(.*?)\s*</a>'
                               r'.*?article__header__text__subtitle">(.*?)</div>', h, re.S)
            for href, title, sub in found:
                spans = [html_to_text(s) for s in re.findall(r"<span>\s*(.*?)\s*</span>", sub, re.S)]
                posted = next((s.replace("Posted:", "").strip() for s in spans if s.startswith("Posted")), "")
                rest = [s for s in spans if not s.startswith("Posted")]
                rows.setdefault(href, (href, html_to_text(title), rest[0] if rest else "", rest[1] if len(rest) > 1 else "", posted))
            if len(found) < 20:
                break

    def detail(r):
        href, title, loc, dept, posted = r
        desc = ""
        try:
            h = fetch(href, retries=1)
            i = h.find("article__content__view")
            desc = html_to_text(h[i:h.find("<footer", i)]) if i > 0 else ""
        except Exception:
            pass
        return job(source="avature", company=firm["name"], category=firm["category"], title=title,
                   location=loc if is_uk(loc) else f"{loc}, United Kingdom", url=href, posted=posted,
                   department=dept, description=desc)

    picked = _relevant_first(list(rows.values()), lambda r: r[1], max_details)
    with ThreadPoolExecutor(6) as ex:
        return list(ex.map(detail, picked))


# ---------------------------------------------------------------- Goldman Sachs (higher.gs.com GraphQL)
_GS_QUERY = ("query GetRoles($searchQueryInput: RoleSearchQueryInput!) { roleSearch(searchQueryInput: $searchQueryInput) "
             "{ totalCount items { roleId corporateTitle jobTitle jobFunction locations { primary state country city } "
             "status division jobType { code description } externalSource { sourceId } } } }")


def goldman(firm):
    out, page, total = [], 0, None
    while page < 15:
        v = {"searchQueryInput": {"page": {"pageSize": 100, "pageNumber": page},
                                  "sort": {"sortStrategy": "RELEVANCE", "sortOrder": "DESC"}, "filters": [],
                                  "experiences": ["EARLY_CAREER", "PROFESSIONAL"], "searchTerm": ""}}
        d = fetch_json("https://api-higher.gs.com/gateway/api/v1/graphql",
                       data={"operationName": "GetRoles", "variables": v, "query": _GS_QUERY},
                       headers={"Origin": "https://higher.gs.com", "Referer": "https://higher.gs.com/"})
        rs = d["data"]["roleSearch"]
        total = rs["totalCount"] if total is None else total
        for r in rs["items"]:
            locs = [l for l in (r.get("locations") or []) if l.get("country") == "United Kingdom"]
            if not locs:
                continue
            place = " / ".join(", ".join(filter(None, [l.get("city"), l.get("country")])) for l in locs)
            out.append(job(
                source="goldman", company=firm["name"], category=firm["category"], title=r.get("jobTitle", ""),
                location=place, url=f"https://higher.gs.com/roles/{r['roleId']}",
                department=" / ".join(filter(None, [r.get("division"), r.get("jobFunction")])),
                description=f"Corporate title: {r.get('corporateTitle') or ''}.",
            ))
        page += 1
        if (page * 100) >= total or not rs["items"]:
            break
    return out


# ---------------------------------------------------------------- Rothschild & Co (own site, server-rendered)
def rothschild(firm, max_details=40):
    site = "https://www.rothschildandco.com"
    rows = {}
    for path in firm.get("paths", ["/en/careers/experienced-professionals/vacancies/"]):
        for page in range(1, 8):
            h = fetch(f"{site}{path}?page={page}")
            cards = re.findall(r'<article class="roths-career-office-event-card">(.*?)</article>', h, re.S)
            for c in cards:
                title = html_to_text((re.search(r"<h3[^>]*>(.*?)</h3>", c, re.S) or [None, ""])[1])
                det = [html_to_text(x) for x in re.findall(r'card-detail">(.*?)</li>', c, re.S)]
                href = (re.search(r'href="([^"]+)"', c) or [None, ""])[1]
                tag = html_to_text((re.search(r'card-tag">(.*?)</span>', c, re.S) or [None, ""])[1])
                place = ", ".join(d for d in det[:2] if d)
                if href and is_uk(" ".join(det)):
                    rows[href] = (title, place, tag, " / ".join(det[2:]), site + href)
            if len(cards) < 40:
                break

    def detail(r):
        title, place, tag, grade, url = r
        desc = ""
        try:
            h = fetch(url, retries=1)
            m = re.search(r"<main[^>]*>(.*?)</main>", h, re.S)
            desc = html_to_text(m.group(1)) if m else ""
        except Exception:
            pass
        return job(source="rothschild", company=firm["name"], category=firm["category"], title=title,
                   location=place if is_uk(place) else f"{place}, United Kingdom", url=url,
                   department=" / ".join(filter(None, [tag, grade])), description=desc)

    picked = _relevant_first(list(rows.values()), lambda r: r[0] + " " + r[2], max_details)
    with ThreadPoolExecutor(6) as ex:
        return list(ex.map(detail, picked))
