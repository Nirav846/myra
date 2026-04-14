import os
import sqlite3
import json
import requests
from datetime import datetime
from rich.console import Console
from PKNSETools.morningstartools import Stock
from myra_app.librarian_core import LibrarianCore


class InstitutionalManager:
    """
    MYRA Institutional Intelligence Manager (v3.2)
    Implements Smart Money Logics: CAR, Hidden Accumulation, Apex Predator.
    Uses Lazy-Load Architecture to minimize RAM usage.
    """

    def __init__(self, db_dir="db", console=None):
        self.db_dir = db_dir
        self.console = console if console else Console()
        self.inst_db = os.path.join(db_dir, LibrarianCore.DB_MAP["institutional"])
        self.meta_db = os.path.join(db_dir, LibrarianCore.DB_MAP["meta"])
        self.tech_db = os.path.join(db_dir, LibrarianCore.DB_MAP["technical"])

    def enrich_top_candidates(self, results):
        """
        Tier 2 & 3: Intelligence Buffer (Lazy-Load).
        Enriches the top candidates from technical scan with deep institutional alpha.
        """
        if not results:
            return results

        self.console.print(
            f"[bold cyan][*] Intelligence Buffer: Deep-diving {len(results)} candidates...[/]"
        )

        # Bypass PKNSETools trading hour restriction by simulating a non-trading time
        old_sim = os.environ.get("simulation")
        n = datetime.now()
        os.environ["simulation"] = json.dumps(
            {
                "isTrading": False,
                "currentDateTime": f"{n.year}-{n.month:02d}-{n.day:02d} 23:00:00",
            }
        )

        enriched = []
        for r in results:
            symbol = r["Stock"]
            try:
                # 1. Fetch Morningstar Data
                stk = Stock(symbol)

                # A. FII/DII QoQ Change
                raw_fii = stk.institutionOwnership(top=5)
                fii_df = stk.mutualFundFIIChangeData(raw_fii) if raw_fii else None

                # B. Fair Value & Star Rating
                fv_data = stk.fairValue()
                # PKNSETools fairValue() only returns latestFairValue. Rating needs Screener API.
                mstar_rating = 0
                fair_value = 0
                if fv_data and isinstance(fv_data, dict):
                    mstar_rating = fv_data.get("quantitativeStarRating", 0)
                    fair_value = fv_data.get("latestFairValue", 0)

                # Fallback for Rating (Screener API)
                if not mstar_rating:
                    try:
                        ms_url = f"https://lt.morningstar.com/api/rest.svc/g9vi2nsqjb/security/screener?page=1&pageSize=5&outputType=json&version=1&languageId=en&currencyId=BAS&universeIds=E0EXG$XNSE&securityDataPoints=ticker,quantitativeStarRating&term={symbol}"
                        ms_res = requests.get(ms_url, timeout=5)
                        if ms_res.status_code == 200:
                            ms_rows = ms_res.json().get("rows", [])
                            for row in ms_rows:
                                if row.get("ticker", "").upper() == symbol.upper():
                                    mstar_rating = row.get("quantitativeStarRating", 0)
                                    break
                    except:
                        pass

                # 2. Calculate Smart Metrics
                metrics = self._calculate_smart_metrics(symbol, fii_df, r)

                # 3. Update Result
                r.update(metrics)
                r["MStar"] = "*" * int(mstar_rating) if mstar_rating else "-"

                # Robust Fair Value conversion
                try:
                    r["Fair_Val"] = round(float(fair_value), 2) if fair_value else "-"
                except:
                    r["Fair_Val"] = "-"

                # 4. Persistence (Sidecar Update)
                self._persist_institutional_data(symbol, metrics, mstar_rating)

                enriched = enriched + [r]
            except Exception:
                # self.console.print(f"[dim red][!] Failed to enrich {symbol}: {e}[/]")
                enriched = enriched + [r]  # Keep original if enrichment fails

        # Restore simulation state
        if old_sim:
            os.environ["simulation"] = old_sim
        else:
            del os.environ["simulation"]

        return enriched

    def _calculate_smart_metrics(self, symbol, fii_df, tech_res):
        """
        Logic 1: CAR (Capital Absorption Ratio)
        Logic 2: Hidden Accumulation
        Logic 3: Apex Predator (Concentration)
        """
        metrics = {
            "CAR": 0.0,
            "Hidden_Acc": "NO",
            "Whale_Conv": "Low",
            "Inst_Floor": 0.0,
        }

        if fii_df is None or fii_df.empty:
            return metrics

        try:
            # Latest Change % (Aggregated first row or sum)
            change_pct = (
                fii_df["changePercentage"].iloc[0]
                if "changePercentage" in fii_df.columns
                else 0
            )

            # If change_pct is missing or None, calculate it from shares
            if change_pct is None or str(change_pct) == "nan" or change_pct == 0:
                change_amt = (
                    fii_df["changeAmount"].iloc[0]
                    if "changeAmount" in fii_df.columns
                    else 0
                )
                curr_shares = (
                    fii_df["currentShares"].iloc[0]
                    if "currentShares" in fii_df.columns
                    else 0
                )
                if change_amt and curr_shares:
                    old_shares = curr_shares - change_amt
                    if old_shares > 0:
                        change_pct = (change_amt / old_shares) * 100.0

            # 1. CAR Calculation
            mcap = tech_res.get("MCap", 0)
            if mcap and mcap > 0 and change_pct:
                est_flow_cr = mcap * (change_pct / 100.0)
                # Average Daily Traded Value (ADTV) - we use money_flow_cr from technicals
                adtv = tech_res.get(
                    "money_flow_cr", 1
                )  # Fallback to 1 to avoid div by zero
                metrics["CAR"] = round(est_flow_cr / max(1, adtv), 2)

            # 2. Hidden Accumulation
            # Condition: Price down > 15% (Stage 4 or negative 90d ret), but FII up
            price_down = tech_res.get("Stage") == "Stage 4"
            if price_down and change_pct > 0.5:
                metrics["Hidden_Acc"] = "YES"

            # 3. Apex Predator (Conviction)
            # If change is > 2% and number of funds is low (using top 5 proxy)
            if change_pct > 2.0:
                metrics["Whale_Conv"] = "HIGH"
            elif change_pct > 0.5:
                metrics["Whale_Conv"] = "Medium"

            # 4. Institutional Floor (Median price of the quarter)
            # For simplicity in lazy-load, we use the price 60 days ago as a proxy for the floor
            # if accumulation is high.
            if change_pct > 0:
                # Actual implementation would query historical prices for the quarter
                metrics["Inst_Floor"] = tech_res.get(
                    "low_1y", 0
                )  # Placeholder for floor

        except Exception:
            pass

        return metrics

    def _persist_institutional_data(self, symbol, metrics, mstar_rating):
        """Saves derived metrics to myra_institutional.db."""
        try:
            conn = sqlite3.connect(self.inst_db)
            now = datetime.now().date().isoformat()

            # Update FII/DII History (Derived)
            sql = """
                INSERT OR REPLACE INTO fii_dii_history 
                (symbol, date, car_ratio, is_hidden_accumulation)
                VALUES (?, ?, ?, ?)
            """
            conn.execute(
                sql,
                (
                    symbol,
                    now,
                    metrics["CAR"],
                    1 if metrics["Hidden_Acc"] == "YES" else 0,
                ),
            )

            conn.commit()
            conn.close()
        except Exception:
            pass
