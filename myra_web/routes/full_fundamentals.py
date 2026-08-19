"""Deep Fundamentals API router.

GET /api/full-fundamentals/{symbol}  -> full fundamentals + rule-based insights.
Accepts ?refresh=true to bypass the 1-hour cache.
"""

import json
import logging
import math
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException

from myra_app.fetchers.full_fundamentals import (
    fetch_full_fundamentals,
    load_cache,
    save_cache,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/full-fundamentals", tags=["full-fundamentals"])

CACHE_TTL_HOURS = 1


# --------------------------------------------------------------------------- #
# Small numeric helpers
# --------------------------------------------------------------------------- #

def _series_values(series):
    if not series:
        return []
    return [p.get("value") for p in series if isinstance(p, dict) and p.get("value") is not None]


def _series_latest(series):
    vals = _series_values(series)
    return vals[-1] if vals else None


def _series_avg(series):
    vals = _series_values(series)
    return sum(vals) / len(vals) if vals else None


def _normalize_debt_to_equity(v):
    """yfinance returns D/E as a percentage for Indian tickers (e.g. 36.65)."""
    if v is None:
        return None
    if v > 5:
        return round(v / 100, 2)
    return round(v, 2)


def _normalize_dividend_yield(v):
    """Snapshot values are already percents; yfinance may return a fraction."""
    if v is None:
        return None
    if v > 1:
        return round(v, 2)
    return round(v * 100, 2)


# --------------------------------------------------------------------------- #
# Graham Number + Defensive Criteria
# --------------------------------------------------------------------------- #

def compute_graham_metrics(data: dict) -> dict:
    """Compute Graham Number and Defensive Criteria from combined fundamentals.

    Returns a dict with:
        graham_number   – conservative intrinsic value (₹) or None
        current_price   – latest close (₹) or None
        margin_of_safety – percentage or None
        defensive       – dict of individual criterion results + overall pass/fail
    """
    ydata = data.get("yfinance", {}) or {}
    snapshot = data.get("snapshot", {}) or {}

    eps = ydata.get("trailing_eps")
    book_value = ydata.get("book_value")
    close = ydata.get("current_price") or snapshot.get("current_price")
    pe = ydata.get("pe") or snapshot.get("pe")
    pb = ydata.get("price_to_book")
    current_ratio = ydata.get("current_ratio")
    de_raw = ydata.get("debt_to_equity")

    # Normalise D/E (yfinance returns percentage for Indian tickers)
    de = None
    if de_raw is not None:
        de = de_raw / 100 if de_raw > 5 else de_raw

    result = {
        "graham_number": None,
        "current_price": close,
        "margin_of_safety": None,
        "defensive": None,
    }

    # --- Graham Number ---
    if eps is not None and book_value is not None and eps > 0 and book_value > 0:
        gn = math.sqrt(22.5 * eps * book_value)
        result["graham_number"] = round(gn, 2)
        if close is not None and close > 0:
            result["margin_of_safety"] = round(((gn - close) / close) * 100, 2)

    # --- Defensive Criteria ---
    if pe is not None and pb is not None:
        checks = {}
        failures = []

        pe_ok = pe < 15
        checks["pe_under_15"] = pe_ok
        if not pe_ok:
            failures.append(f"P/E {pe:.1f} exceeds 15")

        pb_ok = pb < 1.5
        checks["pb_under_1_5"] = pb_ok
        if not pb_ok:
            failures.append(f"P/B {pb:.1f} exceeds 1.5")

        pe_x_pb = pe * pb
        pepb_ok = pe_x_pb < 22.5
        checks["pe_x_pb_under_22_5"] = pepb_ok
        if not pepb_ok:
            failures.append(f"P/E×P/B {pe_x_pb:.1f} exceeds 22.5")

        # Optional harder criteria (only evaluated when data is present)
        if current_ratio is not None:
            cr_ok = current_ratio > 2
            checks["current_ratio_above_2"] = cr_ok
            if not cr_ok:
                failures.append(f"Current ratio {current_ratio:.2f} below 2")

        if de is not None:
            de_ok = de < 1
            checks["debt_equity_under_1"] = de_ok
            if not de_ok:
                failures.append(f"D/E {de:.2f} exceeds 1")

        checks["pass"] = len(failures) == 0
        checks["failures"] = failures
        result["defensive"] = checks

    return result


# --------------------------------------------------------------------------- #
# Piotroski F-Score (6-criterion simplified)
# --------------------------------------------------------------------------- #

def compute_piotroski_score(data: dict) -> dict:
    """Compute a simplified Piotroski F-Score from available data.

    Uses ``ticker.info`` for point-in-time criteria (ROA, CFO, earnings
    quality) and ``ticker.financials`` / ``ticker.balance_sheet`` for the
    YoY-change criteria (current ratio, leverage, gross margin) so both
    years are measured on the same basis.

    Returns ``{score, max_score, classification, criteria}`` where *criteria*
    is a list of ``{name, met, detail}`` dicts.  Returns ``None`` when not
    enough data is available.
    """
    ydata = data.get("yfinance", {}) or {}
    symbol = data.get("symbol")
    if not symbol:
        return None

    try:
        import yfinance as _yf
        ticker = _yf.Ticker(symbol.strip().upper() + ".NS")
    except Exception:
        return None

    # ---- point-in-time values from ticker.info (already fetched) ----
    roa = ydata.get("roa")            # returnOnAssets (fraction)
    cfo = ydata.get("operating_cashflow")
    net_income = ydata.get("net_income")

    # ---- annual financials + balance sheet for YoY changes ----
    fin = None
    bs = None
    try:
        fin = ticker.financials
    except Exception:
        pass
    try:
        bs = ticker.balance_sheet
    except Exception:
        pass

    def _col(df, idx_label, col_pos=0):
        """Safely extract a scalar from a yfinance DataFrame."""
        if df is None:
            return None
        try:
            row = df.loc[idx_label]
            if len(row) <= col_pos:
                return None
            v = row.iloc[col_pos]
            if v is None or (hasattr(v, "isna") and v.isna()):
                return None
            return float(v)
        except Exception:
            return None

    # Current year (index 0) and prior year (index 1) from balance sheet
    cr_now_bs = _col(bs, "Current Ratio", 0)
    cr_prev = _col(bs, "Current Ratio", 1)

    de_now_raw = _col(bs, "Total Debt", 0)
    ta_now = _col(bs, "Total Assets", 0)
    de_prev_raw = _col(bs, "Total Debt", 1)
    ta_prev = _col(bs, "Total Assets", 1)

    de_now = (de_now_raw / ta_now) if de_now_raw and ta_now and ta_now != 0 else None
    de_prev = (de_prev_raw / ta_prev) if de_prev_raw and ta_prev and ta_prev != 0 else None

    # Current year and prior year gross margin from financials
    gm_now_raw = _col(fin, "Gross Profit", 0)
    rev_now = _col(fin, "Total Revenue", 0)
    gm_now = (gm_now_raw / rev_now) if gm_now_raw and rev_now and rev_now != 0 else None

    gm_prev_raw = _col(fin, "Gross Profit", 1)
    rev_prev = _col(fin, "Total Revenue", 1)
    gm_prev = (gm_prev_raw / rev_prev) if gm_prev_raw and rev_prev and rev_prev != 0 else None

    # Fall back to ticker.info for current-year values not on balance sheet
    if cr_now_bs is None:
        cr_now_bs = ydata.get("current_ratio")
    if gm_now is None:
        gm_now = ydata.get("grossMargins")

    # ---- evaluate 6 criteria ----
    criteria = []
    score = 0

    def _check(name, met, detail):
        nonlocal score
        criteria.append({"name": name, "met": met, "detail": detail})
        if met:
            score += 1

    # 1. ROA > 0
    if roa is not None:
        _check("ROA positive", roa > 0, f"ROA = {roa * 100:.1f}%")

    # 2. CFO > 0
    if cfo is not None:
        _check("CFO positive", cfo > 0, f"CFO = {cfo / 1e7:,.0f} Cr")

    # 3. CFO > Net Income (quality of earnings)
    if cfo is not None and net_income is not None:
        _check("CFO > Net Income", cfo > net_income,
               f"CFO {cfo / 1e7:,.0f} Cr vs NI {net_income / 1e7:,.0f} Cr")

    # 4. Current Ratio improving (YoY)
    if cr_now_bs is not None and cr_prev is not None:
        _check("Current ratio improving", cr_now_bs > cr_prev,
               f"{cr_prev:.2f} -> {cr_now_bs:.2f}")

    # 5. Leverage declining (D/E YoY)
    if de_now is not None and de_prev is not None:
        _check("Leverage declining", de_now < de_prev,
               f"D/E {de_prev:.2f} -> {de_now:.2f}")

    # 6. Gross Margin improving (YoY)
    if gm_now is not None and gm_prev is not None:
        _check("Gross margin improving", gm_now > gm_prev,
               f"{gm_prev * 100:.1f}% -> {gm_now * 100:.1f}%")

    max_score = len(criteria)
    if max_score == 0:
        return None

    if score >= 5:
        classification = "Strong"
    elif score >= 3:
        classification = "Moderate"
    else:
        classification = "Weak"

    return {
        "score": score,
        "max_score": max_score,
        "classification": classification,
        "criteria": criteria,
    }


# --------------------------------------------------------------------------- #
# Two-Stage DCF Intrinsic Value
# --------------------------------------------------------------------------- #

def compute_dcf(data: dict) -> dict:
    """Two-stage Discounted Cash Flow model.

    Projects free cash flows for 5 years at a growth rate, then applies
    a Gordon-growth terminal value.  Returns
    ``{fair_value, margin_of_safety, discount_rate, terminal_growth}``
    or ``None`` when inputs are insufficient.
    """
    ydata = data.get("yfinance", {}) or {}
    snapshot = data.get("snapshot", {}) or {}

    fcf = ydata.get("free_cashflow")
    shares = ydata.get("shares_outstanding")
    total_debt = ydata.get("total_debt")
    total_cash = ydata.get("total_cash")
    close = ydata.get("current_price") or snapshot.get("current_price")

    if not all(v is not None and v > 0 for v in (fcf, shares)):
        return None
    if close is None or close <= 0:
        return None

    # Growth rate: prefer revenue growth, fall back to earnings growth, else 5%
    growth = ydata.get("revenue_growth") or ydata.get("earnings_growth")
    if growth is None or growth <= 0:
        growth = 0.05
    # Cap growth at 25% (conservative)
    growth = min(growth, 0.25)

    wacc = 0.10          # 10% discount rate for Indian equities
    terminal_g = 0.03    # 3% perpetual growth
    horizon = 5          # high-growth years

    # Project FCFs for the horizon
    projected = []
    current_fcf = fcf
    for _ in range(horizon):
        current_fcf *= 1 + growth
        projected.append(current_fcf)

    # Terminal value (Gordon Growth)
    terminal_value = projected[-1] * (1 + terminal_g) / (wacc - terminal_g)

    # Discount all cash flows to present value
    pv = 0.0
    for i, cf in enumerate(projected, start=1):
        pv += cf / (1 + wacc) ** i
    pv += terminal_value / (1 + wacc) ** horizon

    # Add net cash to get equity value
    net_cash = (total_cash or 0) - (total_debt or 0)
    equity_value = pv + net_cash

    fair_value = equity_value / shares
    margin_of_safety = ((fair_value - close) / close) * 100

    return {
        "fair_value": round(fair_value, 2),
        "margin_of_safety": round(margin_of_safety, 2),
        "discount_rate": wacc,
        "terminal_growth": terminal_g,
    }


# --------------------------------------------------------------------------- #
# Insights
# --------------------------------------------------------------------------- #

def generate_insights(data: dict) -> list:
    """Rule-based qualitative read of the fundamentals payload."""
    insights = []
    snapshot = data.get("snapshot", {}) or {}
    timeseries = data.get("timeseries", {}) or {}
    ydata = data.get("yfinance", {}) or {}
    shareholding = snapshot.get("shareholding", {}) or {}

    def add(key, title, detail, severity):
        insights.append(
            {"key": key, "title": title, "detail": detail, "severity": severity}
        )

    # 1. PE vs 5-year average
    pe_series = timeseries.get("pe")
    if pe_series:
        avg = _series_avg(pe_series)
        cur = snapshot.get("pe") or _series_latest(pe_series)
        if avg and cur:
            ratio = cur / avg
            if ratio > 2:
                add(
                    "pe",
                    "PE well above historical",
                    f"Current PE {cur:.1f} is more than 2x the 5-year average of {avg:.1f}.",
                    "red",
                )
            elif ratio > 1.2:
                add(
                    "pe",
                    "PE above 5-year average",
                    f"Current PE {cur:.1f} vs 5-year average {avg:.1f}.",
                    "yellow",
                )
            elif ratio < 0.8:
                add(
                    "pe",
                    "PE below 5-year average",
                    f"Current PE {cur:.1f} is trading below the 5-year average of {avg:.1f}.",
                    "green",
                )
            else:
                add(
                    "pe",
                    "PE in line with history",
                    f"Current PE {cur:.1f} vs 5-year average {avg:.1f}.",
                    "green",
                )

    # 2. ROE trend / level
    roe_series = timeseries.get("roe")
    if roe_series and len(roe_series) >= 2:
        earlier = roe_series[0].get("value")
        recent = roe_series[-1].get("value")
        if earlier is not None and recent is not None:
            if recent < earlier:
                add(
                    "roe",
                    "ROE declining",
                    f"ROE fell from {earlier:.1f}% to {recent:.1f}% over the window.",
                    "yellow",
                )
            else:
                add(
                    "roe",
                    "ROE improving",
                    f"ROE rose from {earlier:.1f}% to {recent:.1f}% over the window.",
                    "green",
                )
    else:
        roe = snapshot.get("roe") or ydata.get("roe")
        if roe is not None:
            if roe >= 12:
                add("roe", "Healthy ROE", f"ROE of {roe:.1f}% indicates strong capital efficiency.", "green")
            elif roe >= 8:
                add("roe", "Moderate ROE", f"ROE of {roe:.1f}% is acceptable but not exceptional.", "yellow")
            else:
                add("roe", "Weak ROE", f"ROE of {roe:.1f}% is below healthy thresholds.", "red")

    # 3. ROCE
    roce = snapshot.get("roce") or _series_latest(timeseries.get("roce")) or ydata.get("roce")
    if roce is not None:
        if roce >= 15:
            add("roce", "Strong ROCE", f"ROCE of {roce:.1f}% clears the 15% efficiency bar.", "green")
        elif roce >= 10:
            add("roce", "Decent ROCE", f"ROCE of {roce:.1f}% is reasonable but below 15%.", "yellow")
        else:
            add("roce", "Low ROCE", f"ROCE of {roce:.1f}% signals weak capital returns.", "red")

    # 4. Leverage (Debt/Equity)
    de = _normalize_debt_to_equity(ydata.get("debt_to_equity"))
    if de is not None:
        if de >= 2:
            add("leverage", "Highly leveraged", f"Debt/Equity of {de:.2f} is dangerously high.", "red")
        elif de < 0.5:
            add("leverage", "Low leverage", f"Debt/Equity of {de:.2f} is conservative.", "green")
        else:
            add("leverage", "Moderate leverage", f"Debt/Equity of {de:.2f} is within normal bounds.", "yellow")

    # 5. Promoter holding
    prom = shareholding.get("promoters")
    if prom is not None:
        if prom >= 50:
            add("promoter", "Strong promoter stake", f"Promoters hold {prom:.1f}% — high alignment.", "green")
        elif prom < 30:
            add("promoter", "Low promoter stake", f"Promoters hold only {prom:.1f}%.", "red")
        else:
            add("promoter", "Moderate promoter stake", f"Promoters hold {prom:.1f}%.", "yellow")

    # 6. Analyst recommendation
    rec = str(ydata.get("recommendation_key") or "").lower()
    if rec:
        if rec in ("strong_buy", "buy"):
            add("analyst", "Analysts bullish", "Consensus analyst rating is BUY.", "green")
        elif rec in ("strong_sell", "sell"):
            add("analyst", "Analysts bearish", "Consensus analyst rating is SELL.", "red")
        else:
            add("analyst", "Analysts neutral", "Consensus analyst rating is HOLD.", "yellow")

    # 7. Dividend yield
    dy = _normalize_dividend_yield(snapshot.get("dividend_yield") or ydata.get("dividend_yield"))
    if dy is not None and dy > 0:
        if dy >= 2:
            add("dividend", "Attractive dividend", f"Dividend yield of {dy:.2f}% beats the 2% bar.", "green")
        else:
            add("dividend", "Low dividend yield", f"Dividend yield of {dy:.2f}% is below 2%.", "yellow")

    # 8. Earnings growth
    eg = ydata.get("earnings_growth")
    if eg is not None:
        if eg < 0:
            add("growth", "Earnings contracting", f"Earnings growth is {eg * 100:.1f}% YoY.", "yellow")
        else:
            add("growth", "Earnings growing", f"Earnings growth is {eg * 100:.1f}% YoY.", "green")

    # ------------------------------------------------------------------ #
    # 9–13. Trend-based insights from Screener.in time-series data
    # ------------------------------------------------------------------ #
    _TREND_METRICS = {
        "price_to_book": ("PBV", False),   # valuation: lower = better
        "pe":            ("PE",  False),   # valuation: lower = better
        "roce":          ("ROCE", True),   # efficiency: higher = better
        "roe":           ("ROE",  True),   # efficiency: higher = better
        "market_cap_to_sales": ("P/S", False),  # valuation: lower = better
    }

    for ts_key, (label, higher_is_better) in _TREND_METRICS.items():
        series = timeseries.get(ts_key)
        if not series or len(series) < 3:
            continue

        # Sort by date ascending (should already be, but be safe)
        try:
            sorted_pts = sorted(
                [p for p in series if p.get("date") and p.get("value") is not None],
                key=lambda p: p["date"],
            )
        except Exception:
            continue
        if len(sorted_pts) < 3:
            continue

        latest_date_str = sorted_pts[-1]["date"]
        try:
            from datetime import date as _date
            latest_dt = datetime.fromisoformat(latest_date_str.replace("Z", "")).date()
        except Exception:
            continue

        for window_years in (5, 3):
            cutoff = latest_dt.replace(year=latest_dt.year - window_years)
            window = [p for p in sorted_pts if datetime.fromisoformat(p["date"].replace("Z", "")).date() >= cutoff]
            if len(window) < 2:
                continue

            start_val = window[0]["value"]
            end_val = window[-1]["value"]
            if start_val is None or end_val is None or start_val == 0:
                continue

            pct_change = ((end_val - start_val) / abs(start_val)) * 100
            abs_change = abs(pct_change)
            actual_years = window_years if len(window) >= window_years else len(window)

            # Determine direction text and severity
            if higher_is_better:
                improving = pct_change > 0
                if pct_change > 0:
                    direction = "improved"
                elif pct_change < 0:
                    direction = "declined"
                else:
                    direction = "remained stable"
            else:
                improving = pct_change < 0
                if pct_change < 0:
                    direction = "declined"
                elif pct_change > 0:
                    direction = "increased"
                else:
                    direction = "remained stable"

            if abs_change < 5:
                severity = "yellow"
                direction = "remained stable"
            elif improving:
                severity = "green"
            else:
                severity = "red"

            if abs_change < 5:
                detail = f"{label} has {direction} from {start_val:.1f} to {end_val:.1f} over {actual_years} years."
            else:
                detail = (
                    f"{label} has {direction} from {start_val:.1f} to {end_val:.1f} "
                    f"over {actual_years} years ({pct_change:+.1f}% change)."
                )

            add(
                f"trend_{ts_key}_{window_years}yr",
                f"{label} {actual_years}-year trend",
                detail,
                severity,
            )

    # ------------------------------------------------------------------ #
    # 14–15. Graham Number + Defensive Criteria
    # ------------------------------------------------------------------ #
    graham = compute_graham_metrics(data)
    gn = graham.get("graham_number")
    close = graham.get("current_price")
    mos = graham.get("margin_of_safety")

    if gn is not None and close is not None and close > 0:
        if mos is not None and mos > 10:
            g_sev = "green"
        elif mos is not None and mos > 0:
            g_sev = "yellow"
        else:
            g_sev = "red"
        add(
            "graham_number",
            "Graham fair value",
            f"Graham Number: \u20b9{gn:,.0f}; current: \u20b9{close:,.0f} \u2192 {mos:+.1f}% margin of safety.",
            g_sev,
        )

    defn = graham.get("defensive")
    if defn is not None:
        if defn.get("pass"):
            add(
                "graham_defensive",
                "Passes Graham Defensive Criteria",
                "P/E < 15, P/B < 1.5, P/E\u00d7P/B < 22.5 \u2014 all criteria met.",
                "green",
            )
        else:
            reason = "; ".join(defn.get("failures", []))
            add(
                "graham_defensive",
                "Fails Graham Defensive Criteria",
                reason + ".",
                "red",
            )

    # ------------------------------------------------------------------ #
    # 16. Piotroski F-Score
    # ------------------------------------------------------------------ #
    piotroski = compute_piotroski_score(data)
    if piotroski is not None:
        sc = piotroski["score"]
        mx = piotroski["max_score"]
        cls = piotroski["classification"]
        met = [c["name"] for c in piotroski["criteria"] if c.get("met")]
        unmet = [c["detail"] for c in piotroski["criteria"] if not c.get("met")]
        parts = []
        if met:
            parts.append("Pass: " + ", ".join(met))
        if unmet:
            parts.append("Fail: " + "; ".join(unmet))
        detail_str = " | ".join(parts) if parts else f"{sc}/{mx}"

        if sc >= 5:
            p_sev = "green"
        elif sc >= 3:
            p_sev = "yellow"
        else:
            p_sev = "red"
        add(
            "piotroski",
            f"Piotroski F-Score: {sc}/{mx} \u2014 {cls}",
            detail_str,
            p_sev,
        )

    # ------------------------------------------------------------------ #
    # 17. DCF Intrinsic Value
    # ------------------------------------------------------------------ #
    dcf = compute_dcf(data)
    if dcf is not None:
        fv = dcf["fair_value"]
        mos = dcf["margin_of_safety"]
        close_d = ydata.get("current_price") or snapshot.get("current_price")
        if close_d and close_d > 0:
            label = "undervalued" if mos > 0 else "overvalued"
            if mos > 10:
                d_sev = "green"
            elif mos > 0:
                d_sev = "yellow"
            else:
                d_sev = "red"
            add(
                "dcf",
                "DCF fair value",
                f"DCF intrinsic value: \u20b9{fv:,.0f}; current: \u20b9{close_d:,.0f} \u2192 {mos:+.1f}% {label}.",
                d_sev,
            )

    return insights


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

def _cache_is_fresh(cache_hit) -> bool:
    try:
        updated = datetime.fromisoformat(cache_hit["last_updated"])
        return datetime.now() - updated < timedelta(hours=CACHE_TTL_HOURS)
    except Exception:
        return False


@router.get("/{symbol}")
def get_full_fundamentals(symbol: str, refresh: bool = False):
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="Symbol is required")

    if not refresh:
        cache_hit = load_cache(sym)
        if cache_hit and _cache_is_fresh(cache_hit):
            return {
                "symbol": sym,
                "data": cache_hit["data"],
                "insights": generate_insights(cache_hit["data"]),
                "cached": True,
                "last_updated": cache_hit["last_updated"],
            }

    try:
        data = fetch_full_fundamentals(sym)
    except Exception as e:
        logger.exception("full fundamentals fetch failed for %s", sym)
        raise HTTPException(status_code=500, detail="Internal server error")

    has_data = bool(
        data.get("snapshot")
        or data.get("timeseries")
        or data.get("ratios")
        or data.get("yfinance")
    )
    if not has_data:
        raise HTTPException(status_code=404, detail=f"No data found for symbol {sym}")

    insights = generate_insights(data)
    source = ", ".join(data.get("sources", [])) or "mixed"
    save_cache(sym, json.dumps(data, default=str), source=source)
    return {
        "symbol": sym,
        "data": data,
        "insights": insights,
        "cached": False,
        "last_updated": data.get("timestamp"),
    }
