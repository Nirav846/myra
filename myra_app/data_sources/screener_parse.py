"""Single-pass Screener.in company-page parser.

One HTTP GET of ``https://www.screener.in/company/<SYM>/consolidated/`` returns
the top ratios, the quarterly P&L and the shareholding pattern. Every downstream
consumer used to re-parse (and sometimes re-fetch) that page for one slice of it,
so the profitable move is to parse it once and hand back everything.

This module is deliberately free of I/O: it takes HTML (or a parsed
``BeautifulSoup`` tree) and returns plain dicts. ``DataFetcher._fetch_screener``
and the enrichment adapters both build on it so the label mappings cannot drift.
"""

from __future__ import annotations

import calendar
import re

from bs4 import BeautifulSoup

__all__ = [
    "CRORE",
    "clean_num",
    "canonical_quarterly_key",
    "report_date_sort_key",
    "parse_sector",
    "parse_top_ratios",
    "parse_quarterly",
    "parse_shareholding",
    "parse_company_page",
]

#: Screener.in reports Market Cap in Crores; ``fundamentals.market_cap`` stores
#: raw rupees (verified live: RELIANCE = 1.5837e13). Every crores->rupees
#: conversion must go through this so the two tables cannot disagree.
CRORE = 10_000_000

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name}

#: Screener P&L row label -> canonical column name. Keys are compared after
#: lowercasing and collapsing whitespace, so "Profit before tax" and
#: "Profit Before Tax" both resolve.
QUARTERLY_ROW_MAP = {
    "sales": "revenue",
    "revenue": "revenue",
    "expenses": "expenses",
    "operating profit": "operating_profit",
    "opm %": "opm_pct",
    "opm": "opm_pct",
    "other income": "other_income",
    "interest": "interest",
    "depreciation": "depreciation",
    "profit before tax": "profit_before_tax",
    "tax %": "tax_pct",
    "net profit": "net_profit",
    "eps in rs": "eps",
    "eps": "eps",
}

#: '' -> canonical top-ratio key. Screener renders "Stock P/E", "High / Low",
#: "Dividend Yield" etc. as the visible label.
TOP_RATIO_MAP = {
    "market cap": "market_cap_cr",
    "current price": "current_price",
    "stock p/e": "stock_pe",
    "stock pe": "stock_pe",
    "book value": "book_value",
    "dividend yield": "dividend_yield",
    "roce": "roce",
    "roe": "roe",
    "face value": "face_value",
    "debt to equity": "debt_to_equity",
}

#: Shareholding row label -> canonical key.
SHAREHOLDING_MAP = {
    "promoters": "promoters",
    "fii": "fii",
    "fii s": "fii",
    "dii": "dii",
    "dii s": "dii",
    "government": "government",
    "public": "public",
    "pledged": "pledged",
}


def clean_num(value):
    """Parse a Screener numeric cell. Returns float or None.

    Handles "1,72,762", "15%", "₹ 1,234 Cr.", "-" and the "1,234 / 1,000"
    High/Low cell (callers that care split that string before calling).
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("%", "").strip()
    text = re.sub(r"[^0-9.\-]", "", text)
    # "Cr." trails a bare period; "1,234 Cr." -> "1234.", which float() accepts,
    # but "100.0 Cr." -> "100.0." does not. Strip stray edge dots.
    text = text.strip(".")
    if not text or text in ("-", ".", "-.", ".-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def canonical_quarterly_key(label: str):
    """Canonical column name for a quarterly P&L row label, or None if unused."""
    if not label:
        return None
    return QUARTERLY_ROW_MAP.get(re.sub(r"\s+", " ", label).strip().lower())


def report_date_sort_key(report_date: str):
    """Chronological sort key for a Screener column header.

    Screener headers look like "Sep 2026"; the ISO form "2026-09" is also
    accepted. Sorting these as plain strings is wrong ("Sep 2025" > "Mar 2026"),
    which used to make the *second-newest* quarter win "latest".
    """
    text = str(report_date or "").strip()
    m = re.match(r"(\d{4})-(\d{1,2})", text)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    parts = text.replace("-", " ").split()
    if len(parts) >= 2:
        month = _MONTHS.get(parts[0][:3].lower())
        if month:
            try:
                return (int(parts[1]), month)
            except ValueError:
                pass
    return (0, 0)


def parse_sector(soup: BeautifulSoup):
    """First sector link in the company header (``p.sub a``), or None."""
    sub = soup.find("p", class_="sub")
    if sub is None:
        sub = soup.find("div", id="company-info")
    if sub is None:
        return None
    link = sub.find("a")
    return link.get_text().strip() if link is not None else None


def _text(node) -> str:
    return node.get_text().strip() if node is not None else ""


def _number_for_label(label: str) -> str:
    key = re.sub(r"\s+", " ", label).strip().lower()
    return TOP_RATIO_MAP.get(key, "")


def parse_top_ratios(soup: BeautifulSoup) -> dict:
    """Parse the #top-ratios list into canonical keys.

    Returns a dict that may include market_cap_cr, current_price, high, low,
    stock_pe, book_value, dividend_yield, roce, roe, face_value, debt_to_equity.
    Unknown labels are ignored (they are not worth a schema column).
    """
    out: dict = {}
    container = soup.find(id="top-ratios")
    items = (
        container.find_all("li")
        if container
        else soup.find_all("li", class_=re.compile(r"flex"))
    )
    for li in items:
        name_tag = li.find("span", class_="name")
        value_tag = li.find("span", class_="number")
        if name_tag is None or value_tag is None:
            continue
        label = _text(name_tag)
        raw = _text(value_tag)

        # "High / Low" is a single cell holding two numbers; treat it specially
        # rather than letting clean_num concatenate them into one bogus value.
        if "high" in label.lower() and "low" in label.lower():
            parts = [p for p in re.split(r"[/\\]", raw) if p.strip()]
            if len(parts) >= 2:
                out.setdefault("high", clean_num(parts[0]))
                out.setdefault("low", clean_num(parts[1]))
            continue

        key = _number_for_label(label)
        if not key:
            continue
        value = clean_num(raw)
        if value is not None:
            out[key] = value
    return {k: v for k, v in out.items() if v is not None}


def _quarterly_table(soup: BeautifulSoup):
    section = soup.find(id="quarters")
    if section is not None:
        table = section.find("table")
        if table is not None:
            return table
    for table in soup.find_all("table"):
        parent = table.find_parent("section")
        if parent is not None and "Quarterly" in parent.get_text():
            return table
    return None


def parse_quarterly(soup: BeautifulSoup) -> list[dict]:
    """Parse the #quarters table into one dict per quarter (oldest first).

    Each dict has ``report_date`` (e.g. "Sep 2026") plus every P&L line that
    Screener published, keyed by :data:`QUARTERLY_ROW_MAP`. Unknown rows still
    contribute, keyed by their slugified label, so nothing on the page is lost.
    """
    table = _quarterly_table(soup)
    if table is None:
        return []

    rows = table.find_all("tr")
    if not rows:
        return []

    header_cells = rows[0].find_all(["th", "td"])
    dates = [_text(c) for c in header_cells[1:]]
    dates = [d for d in dates if d]
    if not dates:
        return []

    columns: list[dict] = [{"report_date": d} for d in dates]
    for row in rows[1:]:
        label_tag = row.find(["th", "td"])
        if label_tag is None:
            continue
        raw_label = _text(label_tag)
        if not raw_label:
            continue
        key = canonical_quarterly_key(raw_label) or (
            raw_label.lower().replace(" ", "_")
        )
        cells = row.find_all("td")
        for i, cell in enumerate(cells):
            if i >= len(columns):
                break
            value = clean_num(_text(cell))
            if value is not None:
                columns[i][key] = value
    return columns


def parse_shareholding(soup: BeautifulSoup) -> dict:
    """Parse #quarterly-shp into the LATEST quarter's holder groups.

    Returns keys promoters, fii, dii, public, government, pledged (only those
    present). The last column is the most recent quarter.
    """
    out: dict = {}
    section = soup.find(id="quarterly-shp") or soup.find(id="shareholding")
    if section is None:
        return out
    table = section.find("table")
    if table is None:
        return out
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        label = _text(cells[0]).lower()
        key = None
        for prefix, mapped in SHAREHOLDING_MAP.items():
            if label.startswith(prefix):
                key = mapped
                break
        if key is None:
            continue
        value = clean_num(_text(cells[-1]))
        if value is not None:
            out[key] = value
    # FII + DII is the institutional total the fundamentals table stores.
    if out.get("fii") is not None or out.get("dii") is not None:
        out["inst_holding"] = (out.get("fii") or 0.0) + (out.get("dii") or 0.0)
    return out


def parse_company_page(html: str) -> dict:
    """Parse a Screener company page once and return every section we keep."""
    soup = BeautifulSoup(html, "html.parser")
    return {
        "sector": parse_sector(soup),
        "top": parse_top_ratios(soup),
        "quarterly": parse_quarterly(soup),
        "shareholding": parse_shareholding(soup),
    }
