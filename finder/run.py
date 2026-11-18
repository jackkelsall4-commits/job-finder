"""Find and rank live UK and US jobs (and UK apprenticeships) for the profile in profile.json.

    cd jobs
    python3 -m finder.run                 # everything; writes data/jobs.json and data/report.md
    python3 -m finder.run --top 40        # print more rows
    python3 -m finder.run --only greenhouse,workday
    python3 -m finder.run --probe         # check every configured employer still answers
    python3 -m finder.run --only workday --seed   # on a Mac: save Workday roles to data/seed.json.gz for
                                                  # GitHub to use while Workday refuses its servers

Optional keys (free) widen the search to the whole UK market:
    ADZUNA_APP_ID / ADZUNA_APP_KEY   https://developer.adzuna.com
    REED_API_KEY                     https://www.reed.co.uk/developers
    FAA_API_KEY                      https://developer.apprenticeships.education.gov.uk
"""
import argparse
import gzip
import os
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import scoring, sources
from .http import fetch_json
from .text import in_scope, region_of

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    return json.loads((ROOT / name).read_text())


def plan(cfg, profile, only):
    """List of (label, kind, callable) for every source to query."""
    tasks = []
    for kind in ("greenhouse", "lever", "ashby", "smartrecruiters", "successfactors", "workday", "oracle",
                 "workable", "eightfold", "oleeo", "kpmg", "radancy", "avature", "goldman", "rothschild", "jibe", "gresearch"):
        fn = getattr(sources, kind)
        for firm in cfg.get(kind, []):
            tasks.append((firm["name"], kind, lambda f=firm, fn=fn: _regions(f, fn(f))))
    tasks.append(("Find an apprenticeship (GOV.UK)", "faa",
                  lambda: sources.find_an_apprenticeship(profile["apprenticeship_search_terms"])))
    tasks.append(("Adzuna", "adzuna", lambda: sources.adzuna(profile["search_queries"])))
    tasks.append(("Reed", "reed", lambda: sources.reed(profile["search_queries"])))
    tasks.append(("GOV.UK apprenticeship API", "faa_api", lambda: sources.faa_api(profile["apprenticeship_search_terms"])))
    if only:
        tasks = [t for t in tasks if t[1] in only]
    return tasks


def _regions(firm, rows):
    """A firm can be limited to some countries ("regions": ["UK"]), e.g. when its board name is shared with another company."""
    if not firm.get("regions") or rows is None:
        return rows
    return [r for r in rows if region_of(r.get("location")) in firm["regions"]]


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


def usd_to_gbp():
    """Today's GBP per USD from the ECB reference rates (free, no key); a fixed fallback otherwise."""
    for url in ("https://api.frankfurter.dev/v1/latest?base=USD&symbols=GBP",
                "https://api.frankfurter.app/latest?from=USD&to=GBP"):
        try:
            d = fetch_json(url, retries=1)
            r = float(d["rates"]["GBP"])
            if .5 < r < 1.1:
                return r, f"ECB rate {d.get('date', '')}".strip()
        except Exception:
            continue
    return scoring.USD_TO_GBP, "fixed fallback rate"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma list of source kinds: greenhouse,lever,ashby,smartrecruiters,successfactors,workday,oracle,workable,eightfold,oleeo,kpmg,radancy,avature,goldman,rothschild,jibe,gresearch,faa,adzuna,reed,faa_api")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--probe", action="store_true", help="only report which sources answer")
    ap.add_argument("--out", default=str(ROOT / "data"))
    ap.add_argument("--seed", action="store_true",
                    help="only fetch, and save the roles to data/seed.json.gz (unscored) for runs where those sites refuse the server")
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
            print(f"  {label}: {len(rows)} UK/US roles", file=sys.stderr)
            for r in rows:
                r["src"] = label  # which source it came from, so it can be carried over if that source fails
            raw += rows
    if a.probe:
        for s in sorted(status, key=lambda s: (s["ok"] is not True, s["source"])):
            print(("OK  " if s["ok"] else "--  " if s["ok"] is None else "ERR ") + s["source"], s.get("count", s.get("error")))
        return 0

    # Adzuna/Reed are UK-only already; keep a light location check for the rest.
    raw = [j for j in raw if j["source"] in ("adzuna", "reed", "gov.uk apprenticeships") or in_scope(j["location"])]
    if a.seed:
        return write_seed(Path(a.out) / "seed.json.gz", raw, status)
    raw += from_seed(Path(a.out) / "seed.json.gz", status)
    scoring.USD_TO_GBP, fx_note = usd_to_gbp()
    jobs = [scoring.score(j, profile, aliases) for j in dedupe(raw)]
    prefs = profile["preferences"]
    jobs = [j for j in jobs if j["interest"] >= prefs["min_interest"]]
    jobs += carry_over(Path(a.out) / "jobs.json", status)
    # Rank on the better of today's and post-ACA match so roles that open up after the ACA are kept too.
    jobs.sort(key=lambda j: -max(j["match"], j.get("match_aca", 0)))
    # Each country keeps its own top list so higher US pay doesn't crowd out UK roles.
    # Apprenticeships pay less so would drop off the bottom; keep them as their own list.
    n = prefs["max_results"]
    uk = [j for j in jobs if j["region"] == "UK"]
    us = [j for j in jobs if j["region"] == "US"]
    extra = [j for j in uk[n:] if j["level"] == "apprentice"][:150]
    jobs = sorted(uk[:n] + extra + us[: prefs.get("max_results_us", n // 2)], key=lambda j: -j["match"])
    for j in jobs:
        j["description"] = j["description"][:280]

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(time.time() - t0),
        "sources": sorted(status, key=lambda s: s["source"]),
        "total_seen": len(raw),
        "usd_to_gbp": round(scoring.USD_TO_GBP, 4), "fx_note": fx_note,
        "jobs": jobs,
    }
    (out / "jobs.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False))
    (out / "report.md").write_text(report(payload, 60))
    print(report(payload, a.top))
    return 0


def carry_over(prev_file, status, days=7):
    """A source that failed today (e.g. Workday refusing GitHub's servers) keeps the roles it had
    in the last file for up to a week, marked with the date they were last seen live."""
    failed = {s["source"] for s in status if s["ok"] is False and not s.get("carried")}  # seeded ones are covered
    if not failed or not prev_file.exists():
        return []
    try:
        prev = json.loads(prev_file.read_text())
    except ValueError:
        return []
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    out, kept = [], {}
    for j in prev.get("jobs", []):
        if j.get("src") in failed:
            since = j.get("stale_since") or prev.get("generated_at", "")[:10]
            if since >= cutoff:
                j["stale_since"] = since
                out.append(j)
                kept[j["src"]] = kept.get(j["src"], 0) + 1
    for s in status:
        if s["source"] in kept:
            s["carried"] = kept[s["source"]]
    if out:
        print(f"  kept {len(out)} roles from the last run for {len(kept)} sources that failed today", file=sys.stderr)
    return out


def write_seed(seed_file, raw, status):
    """Save unscored roles fetched on another machine; scoring happens on GitHub with the real profile."""
    for j in raw:
        j["description"] = (j.get("description") or "")[:8000]
    ok = sorted(s["source"] for s in status if s["ok"])
    seed_file.parent.mkdir(parents=True, exist_ok=True)
    seed_file.write_bytes(gzip.compress(json.dumps({"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                                    "sources": ok, "jobs": raw}, ensure_ascii=False).encode()))
    print(f"saved {len(raw)} roles from {len(ok)} sources to {seed_file}")
    failed = [s["source"] for s in status if s["ok"] is False]
    if failed:
        print("failed here too:", ", ".join(failed))
    return 0


def from_seed(seed_file, status, days=7):
    """Roles saved by --seed on another machine, used for sources that refused this run (up to a week old).
    They are scored fresh like today's roles and marked with the date they were fetched."""
    failed = {s["source"] for s in status if s["ok"] is False}
    if not failed or not seed_file.exists():
        return []
    try:
        seed = json.loads(gzip.decompress(seed_file.read_bytes()))
    except (ValueError, OSError):
        return []
    since = seed.get("fetched_at", "")[:10]
    if since < time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400)):
        return []
    out, kept = [], {}
    for j in seed.get("jobs", []):
        if j.get("src") in failed:
            j["stale_since"] = since
            out.append(j)
            kept[j["src"]] = kept.get(j["src"], 0) + 1
    for s in status:
        if s["source"] in kept:
            s["carried"] = kept[s["source"]]
    if out:
        print(f"  used {len(out)} seeded roles for {len(kept)} sources that failed today", file=sys.stderr)
    return out


def report(p, n):
    lines = [f"# Best live UK and US roles for you - {p['generated_at'][:16].replace('T', ' ')} UTC", "",
             f"{p['total_seen']} UK and US vacancies scanned from {sum(1 for s in p['sources'] if s['ok'])} sources; "
             f"{len(p['jobs'])} kept after filtering for your target areas.", "",
             "Match = pay x how reachable it is for you now x fit with your interests x work-from-home bonus. "
             "Reach after ACA is the same score once you are ACA-qualified. "
             f"US pay is converted at $1 = £{p.get('usd_to_gbp', 0):.3f} ({p.get('fx_note', '')}).", "",
             "| # | Role | Employer | Where | Pay | Level | WFH | Reach now | Reach after ACA | Match |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for i, j in enumerate(p["jobs"][:n], 1):
        pay = fmt_money(j["comp"]) + (f" (${j['comp_usd'] / 1000:,.0f}k)" if j.get("comp_usd") else "") + \
            ("" if j["comp_basis"] == "advertised" else " est")
        title = j["title"].replace("|", "/")[:70]
        lines.append(f"| {i} | [{title}]({j['url']}) | {j['company'][:28]} | {j['location'][:24].replace('|', '/')} | "
                     f"{pay} | {j['level']} | {j['workplace_type']} | {int(j['reach'] * 100)}% | {int(j.get('reach_aca', 0) * 100)}% | "
                     f"{fmt_money(j['match'])} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
