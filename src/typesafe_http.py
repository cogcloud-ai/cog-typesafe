"""TypeSafe's documented HTTP API only (https://docs.typesafe.ai/api).

No SDK and no redirects. Error bodies may echo request state, so they are never
repeated: a fault names the status class and, for 422, only the offending field
locations. 429 and 529 are retried with bounded exponential backoff, as the API
reference asks; nothing else is retried, so a turn is never paid for twice.
"""
import http.client
import json
import time
import urllib.error
import urllib.request

API = "https://api.typesafe.ai/v1"
LIMIT = 4 * 1024 * 1024
RETRY_STATUSES = (429, 529)
MAX_RETRIES = 3
MAX_RETRY_AFTER_S = 20


class Fault(ValueError):
    """A failure with a satisfier-binding problem code."""

    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


def _no_constants(name):
    raise ValueError(f"non-finite number {name} in JSON")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _locations(raw):
    """Field locations from a 422 body, never its messages or inputs."""
    try:
        detail = json.loads(raw).get("detail")
    except (ValueError, AttributeError):
        return None
    if not isinstance(detail, list):
        return None
    found = []
    for item in detail[:5]:
        loc = item.get("loc") if isinstance(item, dict) else None
        if isinstance(loc, list) and all(isinstance(x, (str, int)) for x in loc):
            found.append("/".join(str(x) for x in loc)[:120])
    return ", ".join(found) or None


class TypeSafeHTTP:
    def __init__(self, base=API, opener=None, sleep=time.sleep, version="0"):
        self.base = base
        self.opener = opener or urllib.request.build_opener(NoRedirect)
        self.sleep = sleep
        self.agent = f"cog-typesafe/{version}"

    def request(self, method, path, key, body=None, timeout=60, deadline=None):
        headers = {"Accept": "application/json", "Authorization": "Bearer " + key,
                   "User-Agent": self.agent}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, allow_nan=False).encode()
        attempts = 0
        while True:
            attempts += 1
            limit = timeout if deadline is None else min(timeout, deadline - time.monotonic())
            if limit <= 0:
                raise Fault("provider-unavailable", "The turn deadline passed before TypeSafe answered.")
            req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
            try:
                with self.opener.open(req, timeout=limit) as response:
                    raw = response.read(LIMIT + 1)
                if len(raw) > LIMIT:
                    raise Fault("provider-unavailable", "TypeSafe response exceeded the size limit.")
                value = json.loads(raw, parse_constant=_no_constants)
                if not isinstance(value, dict):
                    raise Fault("provider-unavailable", "TypeSafe returned a non-object response.")
                return value, attempts
            except urllib.error.HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get("retry-after") if exc.headers else None
                raw = exc.read(64 * 1024) if status == 422 else b""
                exc.close()
                if status in RETRY_STATUSES and attempts <= MAX_RETRIES:
                    try:
                        wait = min(float(retry_after), MAX_RETRY_AFTER_S) if retry_after else 2 ** (attempts - 1)
                    except ValueError:
                        wait = 2 ** (attempts - 1)
                    self.sleep(max(0.0, wait))
                    continue
                raise self.fault(status, raw) from None
            except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
                if isinstance(exc, Fault):
                    raise
                raise Fault("provider-unavailable", "TypeSafe transport or JSON response failed.") from None

    @staticmethod
    def fault(status, raw=b""):
        if status in (401, 403):
            return Fault("unauthorized", "TypeSafe rejected the API key.")
        if status == 422:
            where = _locations(raw)
            return Fault("invalid-configuration", "TypeSafe rejected the request body"
                         + (f" at {where}." if where else "."))
        if status == 429:
            return Fault("quota-exceeded", "TypeSafe rate limit persisted after retries.")
        if status == 404:
            return Fault("unsupported-model", "TypeSafe endpoint or model was not found.")
        return Fault("provider-unavailable", f"TypeSafe request failed with HTTP {status}.")

    def models(self, key):
        value, _ = self.request("GET", "/models", key, timeout=20)
        return value

    def system_one(self, key, body, deadline=None):
        return self.request("POST", "/systemone", key, body=body, timeout=60, deadline=deadline)
