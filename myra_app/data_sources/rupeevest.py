"""RupeeVest mutual-fund portfolio client (MYRA-vendored, dependency-free).

One ``get_mf_portfolio_tracker`` call per fund returns several months of
*complete* equity holdings (``fincode`` -> company, ``% of AUM``, number of
shares) plus per-month fund AUM and the AMFI classification. That is the
unfiltered source: unlike the published traction artifact (built with
``include_holds=false``) it never drops no-change holdings, so it is what lets
MYRA show *every* stock a fund holds.

Vendored rather than imported from the sibling ``cross-fund-holdings-traction``
repo so MYRA has no runtime coupling to a sibling working tree.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

BASE_URL = "https://www.rupeevest.com"
SEARCH_PATH = "/home/get_search_data"
TRACKER_PATH = "/home/get_mf_portfolio_tracker"
USER_AGENT = "Mozilla/5.0 (compatible; myra/1.0)"

_HTTP_RETRIES = 2
_RETRY_SLEEP = 1.0
_TIMEOUT = 60

_MONTH_ABBR = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}
_MONTH_RE = re.compile(r"^([A-Za-z]{3})-(\d{2})$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _http_get_json(path, params=None, *, timeout=_TIMEOUT):
    """GET ``path`` and decode JSON, retrying transient failures."""
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    last: BaseException | None = None
    for attempt in range(_HTTP_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt < _HTTP_RETRIES:
                time.sleep(_RETRY_SLEEP * (attempt + 1))
                continue
            raise
    raise last  # pragma: no cover - loop always returns or raises


def _token_in(name_l, token):
    """Whole-token match so 'cap' does not hit 'capital'."""
    return (
        re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", name_l) is not None
    )


class RupeeVestIndex:
    """RupeeVest display name -> scheme code, with fuzzy resolution."""

    def __init__(self, by_exact_name: dict[str, str], collisions=()):
        self.by_exact_name = dict(by_exact_name)
        self.collisions = tuple(collisions)
        self._by_lower = {n.lower(): n for n in self.by_exact_name}

    def __len__(self):
        return len(self.by_exact_name)

    def resolve(self, query: str):
        """Return ``(canonical_name, schemecode)`` or ``None``."""
        q = (query or "").strip()
        if not q:
            return None
        if q in self.by_exact_name:
            return q, self.by_exact_name[q]
        hit = self._by_lower.get(q.lower())
        if hit:
            return hit, self.by_exact_name[hit]
        tokens = _TOKEN_RE.findall(q.lower())
        if not tokens:
            return None
        candidates = [
            (len(name), name)
            for name in self.by_exact_name
            if all(_token_in(name.lower(), t) for t in tokens)
        ]
        if not candidates:
            return None
        candidates.sort(reverse=True)
        best = candidates[0][1]
        return best, self.by_exact_name[best]


def index_from_payload(data: dict) -> RupeeVestIndex:
    """Build the index from a ``get_search_data`` payload."""
    by_name: dict[str, str] = {}
    collisions: list[str] = []
    for key in ("search_data", "search_data_nfo"):
        for item in data.get(key) or []:
            name = (item.get("s_name1") or "").strip()
            code = str(item.get("schemecode") or "").strip()
            if not name or not code:
                continue
            existing = by_name.get(name)
            if existing is not None and existing != code:
                collisions.append(f"{name}: kept {existing}, ignored {code}")
                continue
            by_name[name] = code
    return RupeeVestIndex(by_name, collisions)


def load_index() -> RupeeVestIndex:
    data = _http_get_json(SEARCH_PATH)
    if not isinstance(data, dict):
        raise ValueError("RupeeVest search response was not a JSON object")
    return index_from_payload(data)


def fetch_portfolio_tracker(schemecode: str) -> dict:
    data = _http_get_json(TRACKER_PATH, {"schemecode": schemecode})
    if not isinstance(data, dict):
        raise ValueError(f"RupeeVest tracker for {schemecode!r} was not a JSON object")
    return data


def month_to_iso(label: str) -> str | None:
    """``'Aug-26'`` -> ``'2026-08'``; ``None`` when unparseable."""
    m = _MONTH_RE.match((label or "").strip())
    if not m:
        return None
    num = _MONTH_ABBR.get(m.group(1).lower())
    if not num:
        return None
    return f"20{m.group(2)}-{num}"


def fund_slug(name: str) -> str:
    """Stable fund id matching the traction artifact's fund_slug *base*.

    ``'HDFC Small Cap Fund-Reg(G)'`` -> ``'hdfc_small_cap'`` and
    ``'Nippon India Small Cap Fund(G)'`` -> ``'nippon_india_small_cap_fund_g'``
    -- identical to the upstream publisher, so our rows line up with
    ``fund_traction_funds.fund_slug`` (minus its trailing ``_MM_YY``).
    """
    n = re.sub(r"[-–]?\s*Reg\(G\)\s*$", "", (name or "").strip(), flags=re.I)
    n = re.sub(r"\s+Fund\s*$", "", n, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "_", n.lower()).strip("_")


def _to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def parse_tracker(payload: dict, *, schemecode: str = "", fund_name: str = "") -> dict:
    """Normalise a tracker payload into a plain dict (no DB, no network).

    Returns ``{fund_name, schemecode, category, slug, months, aum_cr, holdings}``
    where ``months`` is newest-first ISO ``YYYY-MM`` and ``holdings`` is a flat
    list of ``{month, fincode, company, weight_pct, shares}``.
    """
    raw_months = payload.get("month_name") or []
    months = [month_to_iso(m) for m in raw_months]
    mapping = payload.get("stock_mapping") or {}
    equity = payload.get("stock_data") or []
    aums_raw = payload.get("MonthwiseAUM") or payload.get("MonthWiseAUM") or []
    info = payload.get("fund_info") or []
    name = (fund_name or (info[0].get("s_name") if info else "") or "").strip()
    category = info[0].get("classification") if info else None

    aum_cr: dict[str, float] = {}
    for i, month in enumerate(months):
        if month and i < len(aums_raw):
            aum = _to_float((aums_raw[i] or {}).get("aum"))
            if aum is not None:
                aum_cr[month] = aum

    holdings: list[dict] = []
    for i, block in enumerate(equity):
        if i >= len(months) or not months[i]:
            continue
        month = months[i]
        for item in block or []:
            fin = item.get("fincode")
            if fin is None:
                continue
            fin = str(fin)
            company = str(mapping.get(fin) or "").strip()
            holdings.append(
                {
                    "month": month,
                    "fincode": fin,
                    "company": company or f"fincode:{fin}",
                    "weight_pct": _to_float(item.get("percent_aum")),
                    "shares": _to_float(item.get("noshares")),
                }
            )

    return {
        "fund_name": name,
        "schemecode": str(schemecode or ""),
        "category": category,
        "slug": fund_slug(name),
        "months": [m for m in months if m],
        "aum_cr": aum_cr,
        "holdings": holdings,
    }
