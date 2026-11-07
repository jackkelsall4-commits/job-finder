"""Tiny stdlib HTTP helper with retries, so the finder has no dependencies."""
import base64
import json
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; uk-job-finder/1.0)"


def fetch(url, data=None, headers=None, timeout=25, retries=2, auth=None):
    hdrs = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
        if not isinstance(data, (bytes, str)):
            data = json.dumps(data)
        if isinstance(data, str):
            data = data.encode()
    if auth:
        hdrs["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 401, 403, 404, 422):
                break  # not worth retrying
        except Exception as e:  # network blips, timeouts
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url[:90]} -> {last}")


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw))
