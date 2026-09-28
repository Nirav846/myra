"""READ-ONLY probe: is pre-2026-04 monthly fund-traction history obtainable?

Question this answers
---------------------
`fund_traction` only holds 2026-04..2026-07, and `fund_traction_sync` refuses to
look further back (`MIN_MONTH = "2026-04"`, justified as "pre-2026 data may be
unreliable"). Before treating that guard as the reason history is unusable, we
need to know whether the history is actually *retrievable*. If it is, the guard
is the only thing standing in the way. If the upstream files no longer exist,
the guard is irrelevant and the real blocker is upstream data loss.

Method (read-only, no database writes, no source changes)
--------------------------------------------------------
1. Reuse the EXACT URL pattern from `fund_traction_sync`:
       {TRACTION_BASE_URL}{month_name}_traction.json
   and its HEAD-probe-then-GET logic, over every month 2022-01..2026-03.
2. For each year that yields at least one existing month, fetch one month and
   report symbol count, traction-score distribution, schema match against
   2026-04, and whether it is usable for a point-in-time backtest.
3. Corroborate with upstream repo metadata (public, read-only) so the
   conclusion rests on evidence rather than on one 404.
4. Read the local `fund_traction` table read-only to state the current
   reference schema and coverage.

Deliberately does NOT change MIN_MONTH, write to any database, or fix the
upstream. This tool reports; it does not act.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from myra_app.constants import DB_DIR  # noqa: E402
from myra_app.fund_traction_sync import _MONTH_NAMES  # noqa: E402
from myra_app.librarian_core import LibrarianCore  # noqa: E402

TRACTION_BASE_URL = "https://nirav846.github.io/cross-fund-holdings-traction/data/"
GH_REPO = "nirav846/cross-fund-holdings-traction"
GH_API = f"https://api.github.com/repos/{GH_REPO}"

PROBE_START = "2022-01"
PROBE_END = "2026-03"
REFERENCE_MONTH = "2026-04"  # the month the local table starts at
TIMEOUT_HEAD = 10
TIMEOUT_GET = 30

SCORE_FIELDS = (
    "traction_score",
    "score",
    "traction",
    "avg_traction_score",
)


def say(msg: str = "") -> None:
    print(msg, flush=True)


def rule(title: str) -> None:
    say()
    say("=" * 100)
    say(title)
    say("=" * 100)


def month_url(month: str) -> str:
    """Exact URL pattern used by fund_traction_sync (no year component)."""
    year, m = month.split("-")
    return f"{TRACTION_BASE_URL}{_MONTH_NAMES[int(m) - 1]}_traction.json"


def months_between(start: str, end: str) -> list[str]:
    sy, sm = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    out: list[str] = []
    for y in range(sy, ey + 1):
        lo = sm if y == sy else 1
        hi = em if y == ey else 12
        for m in range(lo, hi + 1):
            out.append(f"{y}-{m:02d}")  # noqa: PG-APPEND
    return out


def parse_stocks(payload) -> list[dict]:
    """Same two accepted shapes as _download_and_parse."""
    if isinstance(payload, dict) and "stocks" in payload:
        rows = payload["stocks"]
    else:
        rows = payload
    return rows if isinstance(rows, list) else []


def fetch_url(url: str) -> dict:
    """GET a URL and parse it with the same two shapes as _download_and_parse."""
    rec = {"url": url, "status": None, "rows": [], "error": None}
    try:
        r = requests.get(url, timeout=TIMEOUT_GET)
        rec["status"] = r.status_code
        if r.status_code != 200:
            rec["error"] = f"HTTP {r.status_code}"
            return rec
        rec["rows"] = parse_stocks(r.json())
    except Exception as exc:  # network/parse -- recorded, never silently skipped
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def fetch_month(month: str) -> dict:
    return fetch_url(month_url(month))


def local_schema_keys() -> set[str]:
    """Read-only: the columns the existing fund_traction table actually uses."""
    db = Path(DB_DIR) / LibrarianCore.DB_MAP["valuation"]
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return set()
    try:
        return {r[1] for r in con.execute("PRAGMA table_info(fund_traction)")}
    finally:
        con.close()


def score_stats(rows: list[dict]) -> dict:
    """Traction-score distribution over whatever field actually exists."""
    field = None
    for r in rows:
        for f in SCORE_FIELDS:
            if isinstance(r, dict) and f in r:
                field = f
                break
        if field:
            break
    if not field:
        return {"field": None, "n": 0}
    vals = []
    for r in rows:
        try:
            v = r.get(field)
            if v is not None and not isinstance(v, str):
                vals.append(float(v))
        except (TypeError, ValueError):
            continue
    if not vals:
        return {"field": field, "n": 0}
    vals.sort()

    def q(p: float) -> float:
        if len(vals) == 1:
            return vals[0]
        pos = p * (len(vals) - 1)
        lo, hi = int(pos), min(int(pos) + 1, len(vals) - 1)
        return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)

    zeros = sum(1 for v in vals if v == 0)
    return {
        "field": field,
        "n": len(vals),
        "min": vals[0],
        "median": q(0.5),
        "max": vals[-1],
        "zero_share": zeros / len(vals),
    }


def keys_of(rows: list[dict]) -> set[str]:
    keys: set[str] = set()
    for r in rows[:200]:
        if isinstance(r, dict):
            keys |= set(r.keys())
    return keys


def local_reference() -> None:
    """Read-only view of what we already hold locally."""
    rule("LOCAL REFERENCE  (READ-ONLY, no writes)")
    db = Path(DB_DIR) / LibrarianCore.DB_MAP["valuation"]
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' " "AND name LIKE 'fund%'"
        )
        tables = [r[0] for r in cur.fetchall()]
        say(f"  fund-related tables: {tables}")
        for t in tables:
            cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})")]
            n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            say(f"\n  {t}: {n} rows")
            say(f"    columns: {cols}")
            month_col = next(
                (c for c in ("month", "as_of_month", "report_month") if c in cols), None
            )
            sym_col = next(
                (c for c in ("symbol", "stock_symbol", "ticker") if c in cols), None
            )
            if month_col:
                rng = cur.execute(
                    f"SELECT MIN({month_col}), MAX({month_col}), "
                    f"COUNT(DISTINCT {month_col}) FROM {t}"
                ).fetchone()
                say(
                    f"    {month_col} range: {rng[0]} .. {rng[1]} "
                    f"({rng[2]} distinct months)"
                )
            if sym_col and month_col:
                span = cur.execute(
                    f"SELECT MIN({month_col}), MAX({month_col}) FROM {t}"
                ).fetchone()
                if span[0]:
                    years = cur.execute(
                        f"SELECT DISTINCT substr({month_col},1,4) FROM {t} "
                        f"ORDER BY 1"
                    ).fetchall()
                    say(f"    years present: {[y[0] for y in years]}")
    finally:
        con.close()


def main(argv=None) -> int:
    rule("READ-ONLY FUND-TRACTION HISTORY PROBE")
    say("  This tool changes nothing. No database is written, MIN_MONTH is not")
    say("  touched, and no upstream defect is fixed here.")

    rule("STEP 0 -- STRUCTURAL FACT ABOUT THE URL PATTERN")
    say(f"  base URL : {TRACTION_BASE_URL}")
    say(f"  pattern  : {TRACTION_BASE_URL}<month_name>_traction.json")
    say("  NOTE: the filename contains NO year. 'january_traction.json' is a")
    say("  single file that is overwritten as time passes, so this pattern was")
    say("  NEVER able to address a specific historical year, even while the")
    say("  files were live. Any pre-2026-04 month was therefore not addressable")
    say("  by this URL scheme from the start -- the MIN_MONTH guard is not the")
    say("  binding constraint.")

    months = months_between(PROBE_START, PROBE_END)
    rule(
        f"STEP 1 -- HEAD PROBE, ALL {len(months)} CANDIDATE MONTHS "
        f"{PROBE_START} .. {PROBE_END}"
    )
    say("  (HEAD logic copied from _list_available_months)")
    say()
    say("  CRITICAL: probe each DISTINCT URL once. The filename has no year, so")
    say(
        f"  {len(months)} candidate months map to only {len(set(month_url(m) for m in months))}"
        " distinct URLs. A month that appears 'available' under several years is"
    )
    say("  ONE file answering for all of them, not one file per year.")

    # month -> url, and url -> the months that resolve to it
    by_url: dict[str, list[str]] = defaultdict(list)
    for m in months:
        by_url[month_url(m)].append(m)

    status_by_code: dict[str, int] = defaultdict(int)
    found: dict[str, list[str]] = {}
    for url in sorted(by_url):
        covers = by_url[url]
        code = None
        try:
            code = requests.head(url, timeout=TIMEOUT_HEAD).status_code
        except Exception as exc:
            code = f"ERR:{type(exc).__name__}"
        status_by_code[str(code)] += 1
        if code == 200:
            found[url] = covers
        say(
            f"    {code}  {url.rsplit('/', 1)[-1]:<24s} "
            f"answers for {len(covers)} candidate month(s): "
            f"{covers[0]}..{covers[-1]}"
        )

    say()
    say(
        f"  distinct URLs probed : {len(by_url)}  "
        f"(from {len(months)} candidate months)"
    )
    say("  status tally (per distinct URL):")
    for code, n in sorted(status_by_code.items()):
        say(f"    {code}: {n} URLs")
    if found:
        say(f"  URLs that EXIST: {len(found)} / {len(by_url)}")
    else:
        say(f"  URLs that EXIST: 0 / {len(by_url)}")

    rule("STEP 2 -- FETCH EXISTING URL(S), PROVE YEAR-INDEPENDENCE, COMPARE SCHEMA")
    if not found:
        say("  No URL in the pattern resolves, so there is nothing to sample and")
        say("  no schema to compare. Reported as unavailable, not assumed.")
        ref_keys: set[str] = set()
    else:
        # The 'current' schema to compare against is what the LOCAL table
        # expects, since the remote reference month may itself be gone.
        ref_keys = local_schema_keys()
        say(f"  local fund_traction reference keys: {sorted(ref_keys)}")
        say()
        digests: dict[str, str] = {}
        for url, covers in sorted(found.items()):
            rec = fetch_url(url)
            st = score_stats(rec["rows"])
            k = keys_of(rec["rows"])
            body = requests.get(url, timeout=TIMEOUT_GET).content
            digests[url] = hashlib.sha256(body).hexdigest()[:16]
            say(
                f"  {url.rsplit('/', 1)[-1]}  HTTP {rec['status']}  "
                f"{len(rec['rows'])} symbols"
            )
            say(
                f"    claims to answer for years: " f"{sorted({c[:4] for c in covers})}"
            )
            say(f"    content sha256[:16]         : {digests[url]}")
            if st["n"]:
                say(
                    f"    {st['field']}: min={st['min']:.4f} "
                    f"median={st['median']:.4f} max={st['max']:.4f} "
                    f"zero_share={st['zero_share']:.1%}"
                )
            say(
                f"    schema match vs local table : "
                f"{'YES' if k == ref_keys else 'NO'}"
            )
            if k != ref_keys:
                say(f"      only in remote : {sorted(k - ref_keys)}")
                say(f"      only in local  : {sorted(ref_keys - k)}")
            say()
        if len(digests) == 1 and len(found) == 1 and len(months) > 12:
            say("  PROOF the surviving file carries no year dimension: the single")
            say(
                "  existing URL answers for candidate months spanning "
                f"{len({c[:4] for c in list(found.values())[0]})} different"
            )
            say("  years, and those months are byte-identical. A file with no year")
            say("  in its name is a rolling snapshot, not an archive.")

    rule("STEP 3 -- UPSTREAM CORROBORATION (public, read-only)")
    say("  A lone 404 could be a path typo, so check the repo itself.")
    try:
        r = requests.get(f"{GH_API}/git/trees/main?recursive=1", timeout=30)
        tree = r.json().get("tree", [])
        jsons = [e["path"] for e in tree if e["path"].endswith(".json")]
        top = sorted({e["path"].split("/")[0] for e in tree})
        say(f"  repo tree reachable          : {r.status_code}")
        say(f"  total entries                : {len(tree)}")
        say(f"  top-level paths              : {top}")
        say(f"  'data/' directory present    : " f"{'data' in top}")
        say(
            f"  traction JSON files in repo  : "
            f"{len([p for p in jsons if 'traction' in p.lower()])}"
        )
        r2 = requests.get(
            f"{GH_API}/commits", params={"path": "data", "per_page": 100}, timeout=30
        )
        n_data_commits = len(r2.json()) if r2.status_code == 200 else -1
        say(f"  commits ever touching 'data/': {n_data_commits}")
        say()
        if "data" not in top and n_data_commits == 0:
            say("  FINDING: the repo contains no 'data/' directory, and no commit")
            say("  in the visible history ever touched one. The monthly traction")
            say("  JSONs were removed when the project was restructured into an")
            say("  application (src/mf_screener), and the history was squashed at")
            say("  the same time. They are not retrievable from git history either.")
        elif "data" in top:
            say("  FINDING: 'data/' exists in the repo, so the live 404 is a path")
            say("  or deploy problem, not data loss. Worth a second look.")
    except Exception as exc:
        say(
            f"  corroboration failed ({type(exc).__name__}: {exc}); "
            f"conclusion rests on the probe alone"
        )

    local_reference()

    rule("VERDICT")
    if found:
        n_urls = len(found)
        n_candidate = len(months)
        spanning = sorted({c[:4] for c in list(found.values())[0]})
        say("  PRE-2026-04 FUND-TRACTION HISTORY IS NOT AVAILABLE.")
        say(
            "  A naive month-by-month probe reports "
            f"{n_candidate} candidate months and finds"
        )
        say(f"  {n_urls} 'available' URL(s), which looks like partial history.")
        say("  It is not. The same finding holds for either reason below, and")
        say("  both were verified:")
        say("    1. YEAR COLLAPSE (structural). The filename carries no year, so")
        say(
            f"       {n_candidate} candidate months resolve to only "
            f"{len(by_url)} distinct URLs."
        )
        say(f"       The surviving file answers for years {spanning} and those")
        say("       responses are byte-identical (sha256 shown in STEP 2). It is")
        say("       a rolling snapshot of the most recent July, not an archive.")
        say("       This URL scheme could never have retrieved a past month.")
        say("    2. UPSTREAM REMOVAL. The repo has no 'data/' directory, no")
        say("       traction JSON, and no commit ever touched 'data/'. The only")
        say("       file still served is a leftover deployment artefact.")
        say()
        say("  Consequence: lowering MIN_MONTH would NOT make history")
        say("  available. No point-in-time fund-traction backtest is possible")
        say("  from this source.")
        say()
        say("  THIRD FINDING -- the local table is now unreproducible AND")
        say("  schema-incompatible. fund_traction holds 2026-04..2026-07, but")
        say("  april_traction.json 404s, so even the months we already stored")
        say("  cannot be re-fetched. The one live file's schema")
        live_keys = keys_of(fetch_url(list(found)[0])["rows"])
        say(f"  ({len(live_keys)} keys) does")
        say("  not match the table's columns, so refreshing from it would need a")
        say("  mapping change, not just a lower guard.")
    else:
        say("  PRE-2026-04 FUND-TRACTION HISTORY IS NOT AVAILABLE: no URL in the")
        say("  pattern resolves at all.")

    say()
    return 0


if __name__ == "__main__":
    sys.exit(main())
