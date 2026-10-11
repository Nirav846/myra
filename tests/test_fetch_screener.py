"""Integration test for DataFetcher._fetch_screener's carry merge.

The parser is unit-tested in isolation (tests/test_screener_parse.py); this
covers the layer that actually decides what gets stored: page-level carry
values applied to every quarterly row, the Crore -> rupee market-cap
conversion, and newest-first ordering.
"""

from myra_app.data_sources.screener_parse import CRORE
from myra_app.fetcher import DataFetcher


class _FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class _FakeSession:
    def __init__(self, text, status_code=200):
        self._resp = _FakeResponse(text, status_code)

    def get(self, url, timeout=None):  # noqa: ARG002 - signature mirror
        return self._resp


def _fetcher(html, status=200):
    # Bypass __init__ so no network/session setup runs; _fetch_screener only
    # touches .session and .last_http_status.
    f = DataFetcher.__new__(DataFetcher)
    f.session = _FakeSession(html, status)
    f.last_http_status = None
    return f


_PAGE = """
<html><body>
  <p class="sub"><a href="/sector/metals">Metals &amp; Mining</a></p>
  <div id="top-ratios">
    <li class="flex"><span class="name">Market Cap</span>
        <span class="number">\u20b9 1,72,762 Cr.</span></li>
    <li class="flex"><span class="name">Current Price</span>
        <span class="number">\u20b9 2,955</span></li>
    <li class="flex"><span class="name">Stock P/E</span>
        <span class="number">22.5</span></li>
    <li class="flex"><span class="name">Book Value</span>
        <span class="number">1,200</span></li>
    <li class="flex"><span class="name">ROE</span>
        <span class="number">9.5%</span></li>
    <li class="flex"><span class="name">High / Low</span>
        <span class="number">3,024 / 2,205</span></li>
  </div>
  <section id="quarters">
    <table>
      <thead><tr><th>Particulars</th><th>Mar 2026</th><th>Dec 2025</th></tr></thead>
      <tbody>
        <tr><th>Sales</th><td>2,50,000</td><td>2,40,000</td></tr>
        <tr><th>Net Profit</th><td>18,000</td><td>17,000</td></tr>
        <tr><th>EPS in Rs</th><td>13.3</td><td>12.6</td></tr>
      </tbody>
    </table>
  </section>
  <section id="quarterly-shp">
    <table>
      <thead><tr><th></th><th>Mar 2026</th></tr></thead>
      <tbody>
        <tr><td>Promoters</td><td>50.3%</td></tr>
        <tr><td>FII</td><td>22.0%</td></tr>
        <tr><td>DII</td><td>15.0%</td></tr>
      </tbody>
    </table>
  </section>
</body></html>
"""


def test_fetch_screener_applies_carry_and_converts_crore():
    results = _fetcher(_PAGE)._fetch_screener("RELIANCE")

    assert len(results) == 2
    # Newest quarter first, despite "Dec 2025" sorting above "Mar 2026" as text.
    assert [r["report_date"] for r in results] == ["Mar 2026", "Dec 2025"]

    latest = results[0]
    # Page-level carry is applied to every quarter row.
    assert latest["market_cap"] == 172762 * CRORE
    assert latest["stock_pe"] == 22.5
    assert latest["book_value"] == 1200.0
    assert latest["roe"] == 9.5
    assert latest["high"] == 3024.0 and latest["low"] == 2205.0
    assert latest["promoter_holding"] == 50.3
    assert latest["inst_holding"] == 37.0  # FII 22 + DII 15
    assert latest["sector"] == "Metals & Mining"
    # ... and the row's own quarterly values are preserved.
    assert latest["revenue"] == 250000.0
    assert latest["net_profit"] == 18000.0

    older = results[1]
    assert older["revenue"] == 240000.0
    assert older["market_cap"] == 172762 * CRORE


def test_fetch_screener_returns_none_without_quarters():
    html = """
    <html><body>
      <div id="top-ratios">
        <li class="flex"><span class="name">Stock P/E</span>
            <span class="number">22.5</span></li>
      </div>
    </body></html>
    """
    assert _fetcher(html)._fetch_screener("RELIANCE") is None
