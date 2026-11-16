"""Tiny stdlib HTTP helper with retries, so the finder has no dependencies."""
import base64
import gzip
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (compatible; uk-job-finder/1.0)"

# Workday hosts every bank's careers site on shared servers that rate-limit by caller, so
# 17 firms fetched in parallel trip "429 Too Many Requests". Keep a few requests in flight
# across all Workday sites, spaced out, and back off whenever one says slow down. If it keeps
# refusing after that (it sometimes blocks cloud servers such as GitHub's outright), stop asking
# for the rest of the run so the other sources aren't held up; run.py then reuses the last good roles.
_THROTTLED = {"myworkdayjobs.com": (3, 0.25)}  # domain: (max requests at once, min gap between starts, s)
_slots = {d: threading.BoundedSemaphore(n) for d, (n, _) in _THROTTLED.items()}
_gate = threading.Lock()
_next_start = {d: 0.0 for d in _THROTTLED}
_cooldown = {d: 0.0 for d in _THROTTLED}  # time before which nobody should hit this domain
_refusals = {d: 0 for d in _THROTTLED}  # requests that gave up on 429 this run
BLOCK_AFTER = 3  # give up on a domain for the rest of the run after this many


def _domain(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    return next((d for d in _THROTTLED if host == d or host.endswith("." + d)), None)


def _wait_turn(dom):
    with _gate:
        now = time.monotonic()
        start = max(now, _next_start[dom], _cooldown[dom])
        _next_start[dom] = start + _THROTTLED[dom][1]
    if start > now:
        time.sleep(start - now)


def _retry_after(e, attempt):
    """Seconds to wait after a 429: the server's Retry-After if it gives one, else 10s then 30s."""
    try:
        v = float(e.headers.get("Retry-After") or "")
    except (TypeError, ValueError):
        v = (10, 30)[min(attempt, 1)]
    return min(max(v, 2), 60)


def fetch(url, data=None, headers=None, timeout=25, retries=2, auth=None):
    hdrs = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9", "Accept-Encoding": "gzip"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
        if not isinstance(data, (bytes, str)):
            data = json.dumps(data)
        if isinstance(data, str):
            data = data.encode()
    if auth:
        hdrs["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    hdrs.update(headers or {})
    dom = _domain(url)
    if dom and _refusals[dom] >= BLOCK_AFTER:
        raise RuntimeError(f"{url[:90]} -> skipped: {dom} kept refusing this server (429 Too Many Requests)")
    last = None
    rate_limited = 0
    attempt = 0
    while attempt <= retries:
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            if dom:
                with _slots[dom]:
                    _wait_turn(dom)
                    resp = urllib.request.urlopen(req, timeout=timeout)
            else:
                resp = urllib.request.urlopen(req, timeout=timeout)
            with resp:
                body = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
                    body = gzip.decompress(body)
                return body.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 404, 422):
                break  # not worth retrying
            if e.code == 429:
                # Rate limited: wait it out (twice) without using up the normal retries,
                # and make every other request to the same site wait too.
                if rate_limited >= 2 or (dom and _refusals[dom] >= BLOCK_AFTER):
                    if dom:
                        with _gate:
                            _refusals[dom] += 1
                    break
                pause = _retry_after(e, rate_limited)
                rate_limited += 1
                if dom:
                    with _gate:
                        _cooldown[dom] = max(_cooldown[dom], time.monotonic() + pause)
                time.sleep(pause)
                continue
            if e.code in (401, 403):
                # Workday sites sometimes refuse a request briefly (seen with Blackstone);
                # try once more after a pause, but don't hammer a site that really blocks us.
                if attempt >= 1:
                    break
                time.sleep(6)
                attempt += 1
                continue
        except Exception as e:  # network blips, timeouts
            last = e
        time.sleep(1.5 * (attempt + 1))
        attempt += 1
    raise RuntimeError(f"{url[:90]} -> {last}")


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw))
