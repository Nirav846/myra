"""Unit tests for the single-pass Screener.in company-page parser."""

from bs4 import BeautifulSoup

from myra_app.data_sources.screener_parse import (
    CRORE,
    clean_num,
    parse_company_page,
    parse_quarterly,
    parse_shareholding,
    parse_top_ratios,
    report_date_sort_key,
)


def test_clean_num_handles_indian_grouping():
    assert clean_num("1,72,762 Cr.") == 172762.0
    assert clean_num("₹ 1,234") == 1234.0
    assert clean_num("15%") == 15.0
    assert clean_num("-") is None
    assert clean_num("") is None
    assert clean_num(None) is None
    assert clean_num(12.5) == 12.5


def test_report_date_sort_key_is_chronological_not_lexical():
    # "Sep 2025" sorts AFTER "Mar 2026" as strings but BEFORE it chronologically.
    assert report_date_sort_key("Mar 2026") > report_date_sort_key("Sep 2025")
    assert report_date_sort_key("2026-09") == (2026, 9)
    assert report_date_sort_key("garbage") == (0, 0)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_parse_top_ratios_maps_labels_and_splits_high_low():
    html = """
    <li id="top-ratios-0" class="flex flex-space-between">
        <span class="name">Market Cap</span><span class="number">₹ 1,72,762 Cr.</span>
    </li>
    <li class="flex flex-space-between">
        <span class="name">Stock P/E</span><span class="number">22.5</span>
    </li>
    <li class="flex flex-space-between">
        <span class="name">Book Value</span><span class="number">90.0</span>
    </li>
    <li class="flex flex-space-between">
        <span class="name">Dividend Yield</span><span class="number">1.2 %</span>
    </li>
    <li class="flex flex-space-between">
        <span class="name">High / Low</span><span class="number">2,500 / 2,100</span>
    </li>
    """
    soup = _soup(f'<div id="top-ratios">{html}</div>')
    out = parse_top_ratios(soup)
    assert out["market_cap_cr"] == 172762.0
    assert out["stock_pe"] == 22.5
    assert out["book_value"] == 90.0
    assert out["dividend_yield"] == 1.2
    assert out["high"] == 2500.0
    assert out["low"] == 2100.0
    assert out["high"] + out["low"] != 4100_000_000  # never concatenated


def test_parse_quarterly_extracts_every_pnl_line_per_quarter():
    html = """
    <section id="quarters">
      <table>
        <thead><tr>
          <th>Particulars</th><th>Sep 2026</th><th>Jun 2026</th>
        </tr></thead>
        <tbody>
          <tr><th class="text-left">Sales</th><td>1,00,000</td><td>80,000</td></tr>
          <tr><th class="text-left">Expenses</th><td>90,000</td><td>72,000</td></tr>
          <tr><th class="text-left">Operating Profit</th><td>10,000</td><td>8,000</td></tr>
          <tr><th class="text-left">OPM %</th><td>10%</td><td>10%</td></tr>
          <tr><th class="text-left">Other Income</th><td>500</td><td>400</td></tr>
          <tr><th class="text-left">Interest</th><td>200</td><td>150</td></tr>
          <tr><th class="text-left">Depreciation</th><td>700</td><td>600</td></tr>
          <tr><th class="text-left">Profit before tax</th><td>9,600</td><td>7,650</td></tr>
          <tr><th class="text-left">Tax %</th><td>25%</td><td>25%</td></tr>
          <tr><th class="text-left">Net Profit</th><td>7,200</td><td>5,737</td></tr>
          <tr><th class="text-left">EPS in Rs</th><td>3.0</td><td>2.4</td></tr>
        </tbody>
      </table>
    </section>
    """
    rows = parse_quarterly(_soup(html))
    assert [r["report_date"] for r in rows] == ["Sep 2026", "Jun 2026"]
    assert rows[0]["revenue"] == 100000.0
    assert rows[0]["expenses"] == 90000.0
    assert rows[0]["operating_profit"] == 10000.0
    assert rows[0]["opm_pct"] == 10.0
    assert rows[0]["other_income"] == 500.0
    assert rows[0]["interest"] == 200.0
    assert rows[0]["depreciation"] == 700.0
    assert rows[0]["profit_before_tax"] == 9600.0
    assert rows[0]["tax_pct"] == 25.0
    assert rows[0]["net_profit"] == 7200.0
    assert rows[0]["eps"] == 3.0
    assert rows[1]["revenue"] == 80000.0
    assert rows[1]["net_profit"] == 5737.0


def test_parse_shareholding_uses_latest_quarter_and_derives_inst_holding():
    html = """
    <section id="quarterly-shp">
      <table>
        <thead><tr><th></th><th>Jun 2025</th><th>Jun 2026</th></tr></thead>
        <tbody>
          <tr><td>Promoters</td><td>50.0%</td><td>45.0%</td></tr>
          <tr><td>FIIs</td><td>20.0%</td><td>22.0%</td></tr>
          <tr><td>DIIs</td><td>10.0%</td><td>11.0%</td></tr>
          <tr><td>Public</td><td>20.0%</td><td>22.0%</td></tr>
          <tr><td>Pledged</td><td>0.0%</td><td>2.5%</td></tr>
        </tbody>
      </table>
    </section>
    """
    out = parse_shareholding(_soup(html))
    assert out["promoters"] == 45.0  # latest column, not 50.0
    assert out["fii"] == 22.0
    assert out["dii"] == 11.0
    assert out["public"] == 22.0
    assert out["pledged"] == 2.5
    assert out["inst_holding"] == 33.0


def test_parse_company_page_returns_every_section():
    html = f"""
    <p class="sub"><a href="/sector/metals" style="color: #666">Metals & Mining</a></p>
    <div id="top-ratios">
      <li class="flex"><span class="name">Market Cap</span><span class="number">₹ {100.0} Cr.</span></li>
      <li class="flex"><span class="name">Stock P/E</span><span class="number">15</span></li>
    </div>
    <section id="quarters">
      <table><thead><tr><th>Particulars</th><th>Sep 2026</th></tr></thead>
      <tbody><tr><th>Sales</th><td>100</td></tr></tbody></table>
    </section>
    <section id="quarterly-shp">
      <table><thead><tr><th></th><th>Sep 2026</th></tr></thead>
      <tbody><tr><td>Promoters</td><td>50.0%</td></tr></tbody></table>
    </section>
    """
    page = parse_company_page(html)
    assert page["sector"] == "Metals & Mining"
    assert page["top"]["market_cap_cr"] == 100.0
    assert page["quarterly"][0]["report_date"] == "Sep 2026"
    assert page["shareholding"]["promoters"] == 50.0
    assert CRORE == 10_000_000
