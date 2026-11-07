"""Find and rank live UK jobs and apprenticeships for the profile in profile.json.

    cd jobs
    python3 -m finder.run                 # everything; writes data/jobs.json and data/report.md
    python3 -m finder.run --top 40        # print more rows
    python3 -m finder.run --only greenhouse,workday
    python3 -m finder.run --probe         # check every configured employer still answers

Optional keys (free) widen the search to the whole UK market:
    ADZUNA_APP_ID / ADZUNA_APP_KEY   https://developer.adzuna.com
    REED_API_KEY                     https://www.reed.co.uk/developers
    FAA_API_KEY                      https://developer.apprenticeships.education.gov.uk
"""
import argparse
import os
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import scoring, sources
from .text import is_uk

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    return json.loads((ROOT / name).read_text())


def plan(cfg, profile, only):
    """List of (label, kind, callable) for every source to query."""
    tasks = []
    for kind in ("greenhouse", "lever", "ashby", "smartrecruiters", "successfactors", "workday", "oracle"):
        fn = getattr(sources, kind)
        for firm in cfg.get(kind, []):
            tasks.append((firm["name"], kind, lambda f=firm, fn=fn: fn(f)))
    tasks.append(("Find an apprenticeship (GOV.UK)", "faa",
                  lambda: sources.find_an_apprenticeship(profile["apprenticeship_search_terms"])))
    tasks.append(("Adzuna", "adzuna", lambda: sources.adzuna(profile["search_queries"])))
    tasks.append(("Reed", "reed", lambda: sources.reed(profile["search_queries"])))
    tasks.append(("GOV.UK apprenticeship API", "faa_api", lambda: sources.faa_api(profile["apprenticeship_search_terms"])))
    if only:
        tasks = [t for t in tasks if t[1] in only]
    return tasks


def dedupe(jobs):
    seen, out = {}, []
    for j in jobs:
        key = (re.sub(r"\W+", "", j["company"].lower())[:14], re.sub(r"\W+", "", j["title"].lower()),
               re.sub(r"\W+", "", (j["location"] or "").lower())[:10])
        if key in seen:
            prev = seen[key]
            if not prev.get("salary_min") and j.get("salary_min"):  # keep the one with a salary
                out[out.index(prev)] = j
                seen[key] = j
            continue
        seen[key] = j
        out.append(j)
    return out


def fmt_money(v):
    return f"£{v / 1000:,.0f}k" if v else "-"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma list of source kinds: greenhouse,lever,ashby,smartrecruiters,successfactors,workday,oracle,faa,adzuna,reed,faa_api")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--probe", action="store_true", help="only report which sources answer")
    ap.add_argument("--out", default=str(ROOT / "data"))
    a = ap.parse_args(argv)

    # Your real profile can live in a PROFILE_JSON secret so it never appears in a public repo.
    if os.environ.get("PROFILE_JSON"):
        profile = json.loads(os.environ["PROFILE_JSON"])
    else:
        profile = load("profile.json" if (ROOT / "profile.json").exists() else "profile.example.json")
    cfg = load("sources.json")
    aliases = cfg["firm_aliases"]
    tasks = plan(cfg, profile, set(a.only.split(",")) if a.only else None)
    t0 = time.time()
    raw, status = [], []
    with ThreadPoolExecutor(10) as ex:
        futs = {ex.submit(fn): (label, kind) for label, kind, fn in tasks}
        for f in as_completed(futs):
            label, kind = futs[f]
            try:
                rows = f.result()
            except Exception as e:
                status.append({"source": label, "kind": kind, "ok": False, "error": str(e)[:200]})
                print(f"  ! {label}: {str(e)[:120]}", file=sys.stderr)
                continue
            if rows is None:
                status.append({"source": label, "kind": kind, "ok": None, "error": "no API key set (optional)"})
                continue
            status.append({"source": label, "kind": kind, "ok": True, "count": len(rows)})
            print(f"  {label}: {len(rows)} UK roles", file=sys.stderr)
            raw += rows
    if a.probe:
        for s in sorted(status, key=lambda s: (s["ok"] is not True, s["source"])):
            print(("OK  " if s["ok"] else "--  " if s["ok"] is None else "ERR ") + s["source"], s.get("count", s.get("error")))
        return 0

    # Adzuna/Reed are UK-only already; keep a light location check for the rest.
    raw = [j for j in raw if j["source"] in ("adzuna", "reed", "gov.uk apprenticeships") or is_uk(j["location"])]
    jobs = [scoring.score(j, profile, aliases) for j in dedupe(raw)]
    prefs = profile["preferences"]
    jobs = [j for j in jobs if j["interest"] >= prefs["min_interest"]]
    jobs.sort(key=lambda j: -j["match"])
    # Apprenticeships pay less so would drop off the bottom; keep them as their own list.
    top = jobs[: prefs["max_results"]]
    extra = [j for j in jobs[prefs["max_results"]:] if j["level"] == "apprentice"][:150]
    jobs = top + extra
    for j in jobs:
        j["description"] = j["description"][:280]

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(time.time() - t0),
        "sources": sorted(status, key=lambda s: s["source"]),
        "total_seen": len(raw),
        "jobs": jobs,
    }
    (out / "jobs.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    (out / "report.md").write_text(report(payload, 60))
    print(report(payload, a.top))
    return 0


def report(p, n):
    lines = [f"# Best live UK roles for you - {p['generated_at'][:16].replace('T', ' ')} UTC", "",
             f"{p['total_seen']} UK vacancies scanned from {sum(1 for s in p['sources'] if s['ok'])} sources; "
             f"{len(p['jobs'])} kept after filtering for your target areas.", "",
             "Match = pay x how reachable it is for you now x fit with your interests x work-from-home bonus.", "",
             "| # | Role | Employer | Where | Pay | Level | WFH | Reach | Match |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, j in enumerate(p["jobs"][:n], 1):
        pay = fmt_money(j["comp"]) + ("" if j["comp_basis"] == "advertised" else " est")
        title = j["title"].replace("|", "/")[:70]
        lines.append(f"| {i} | [{title}]({j['url']}) | {j['company'][:28]} | {j['location'][:24].replace('|', '/')} | "
                     f"{pay} | {j['level']} | {j['workplace_type']} | {int(j['reach'] * 100)}% | {fmt_money(j['match'])} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
