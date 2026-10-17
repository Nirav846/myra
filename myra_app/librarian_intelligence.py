#!/usr/bin/env python
"""
MYRA Librarian Intelligence Layer (TRILOGY ERA)
Handles all heavy indicator computation using Parquet Lake.
"""

import logging
import os
import time

import numpy as np
import pandas as pd
import pandas_ta as ta

from myra_core.utils.data_validation import enforce_index_contract, validate_dataframe
from myra_core.utils.myra_log import myra_log

logger = logging.getLogger(__name__)


def safe_concat(df_list):
    clean = [
        df
        for df in df_list
        if df is not None and not df.empty and not df.isnull().all().all()
    ]
    if not clean:
        return pd.DataFrame()
    # Drop all-NaN columns from each DataFrame BEFORE concat (stops FutureWarning)
    clean = [df.dropna(axis=1, how="all") for df in clean]
    return pd.concat(clean, axis=0, ignore_index=True)


class LibrarianIntelligenceMixin:
    def precompute_indicators(self, as_of_date=None):
        """
        Unified API for retrieving indicators via Parquet Lake.
        FIXED: No duplicate index, safe concat, explicit schema.
        """
        active_symbols = self.get_active_universe()
        if not active_symbols:
            return pd.DataFrame()

        results_list = []

        for sym in active_symbols:
            try:
                df_node = self.loader.indicators.load_indicators("precomputed", sym)

                if df_node is None or df_node.empty:
                    continue

                df_node = enforce_index_contract(df_node, symbol=sym)

                if as_of_date:
                    df_node = df_node[df_node.index <= as_of_date]

                if df_node.empty:
                    continue

                # 🔥 TAKE LAST ROW SAFELY
                row = df_node.iloc[[-1]].copy()

                # 🔥 CRITICAL: move index → column
                row = row.reset_index()

                # Ensure symbol exists
                row["symbol"] = sym

                results_list.append(row)  # noqa: PG-APPEND

            except Exception as e:
                logger.debug(f"Failed to load indicator for {sym}: {e}")
                continue

        df_final = safe_concat(results_list)

        # No usable rows is a legitimate outcome, not a contract violation.
        #
        # This happens whenever the lake has not been built yet, and also when
        # ``as_of_date`` precedes every stored row. Both callers of this method
        # already handle an empty frame explicitly (``universe_loader`` prints a
        # staleness notice; the scorer treats it as "no candidates"), so the
        # correct behaviour is to *return* the empty frame the callers expect.
        #
        # It used to ``assert "symbol" in df_final.columns`` here, which raised
        # ``AssertionError`` out of a read path and unwound through
        # ``load_universe`` — turning "the indicator lake is empty" into an
        # opaque crash instead of the empty result the callers were written for.
        if df_final.empty:
            logger.warning(
                "[MYRA] Indicator lake returned no usable rows for %d active "
                "symbols (lake empty or as_of_date=%s precedes all stored rows).",
                len(active_symbols),
                as_of_date,
            )
            return pd.DataFrame()

        assert "symbol" in df_final.columns
        assert "date" in df_final.columns

        df_final = df_final.set_index(["symbol", "date"], drop=False)

        if not df_final.index.is_unique:
            dupes = df_final[df_final.index.duplicated(keep=False)]
            raise Exception(
                f"Duplicate (symbol, date) detected after aggregation:\n{dupes.tail(10)}"
            )

        return df_final

    def _compute_symbol_indicators(self, sym):
        """Compute the full indicator frame for one symbol.

        Single source of truth for what the indicator lake holds: both the
        whole-universe rebuild (``update_indicator_history``) and the bounded,
        freshness-gated build (``build_indicator_lake``) go through here, so the
        two can never drift into subtly different indicator definitions.

        Returns the frame ready to save, or ``None`` when the symbol has no
        usable history. Exceptions are left to propagate: a failure for one
        symbol is the caller's to count and report, not this method's to hide
        behind a defaulted value.
        """
        df = self.get_ohlcv(sym)

        if df is None or len(df) < 30:
            return None

        df.columns = [c.capitalize() for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]

        # Normalize index
        df.index = pd.to_datetime(df.index, errors="coerce").normalize()
        df = df[df.index.notna()]

        df.sort_index(inplace=True)
        df = df.loc[~df.index.duplicated(keep="last")]

        # 🔥 dtype safety (fix warning + hidden bugs)
        for col in ["Volume", "Delivery_qty"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Contract enforcement
        df = enforce_index_contract(df, symbol=sym)

        df = validate_dataframe(df, context=f"Indicator Engine: {sym}")

        # Core calculations
        df["inst_vol"] = 0.0

        df["sma20"] = ta.sma(df["Close"], length=20)
        df["sma50"] = ta.sma(df["Close"], length=50)
        df["sma200"] = ta.sma(df["Close"], length=200)
        df["rsi"] = ta.rsi(df["Close"], length=14)
        df["atr20"] = ta.atr(df["High"], df["Low"], df["Close"], length=20)

        df["vol_sma50"] = ta.sma(df["Volume"], length=50)

        if "Delivery_qty" in df.columns:
            df["avg_deliv_20d"] = ta.sma(df["Delivery_qty"], length=20)
            df["rdv"] = df["Delivery_qty"] / df["avg_deliv_20d"].replace(0, 1)
        else:
            df["rdv"] = 1.0

        try:
            if "Delivery_qty" in df.columns and "Volume" in df.columns:
                df["delivery_pct"] = (
                    df["Delivery_qty"] / df["Volume"].replace(0, np.nan)
                ).fillna(0.0) * 100.0
            elif "Delivery_pct" in df.columns:
                df["delivery_pct"] = df["Delivery_pct"].fillna(0.0)
            else:
                df["delivery_pct"] = 0.0

            df["vcp"] = 1.0 - (df["atr20"] / df["sma20"].replace(0, np.nan)).fillna(0.0)
            df["vcp"] = df["vcp"].clip(0.0, 1.0)

            rolling_mean = df["delivery_pct"].rolling(20, min_periods=1).mean()
            rolling_std = (
                df["delivery_pct"]
                .rolling(20, min_periods=1)
                .std()
                .replace(0, np.nan)
                .fillna(1.0)
            )

            df["delivery_divergence_score"] = (
                (df["delivery_pct"] - rolling_mean) / rolling_std
            ).fillna(0.0)
            df["delivery_divergence_score"] *= df["vcp"]

            try:
                df["vwap"] = ta.vwap(df["High"], df["Low"], df["Close"], df["Volume"])
            except Exception:
                pv = (df["Close"] * df["Volume"]).rolling(20).sum()
                v = df["Volume"].rolling(20).sum().replace(0, np.nan)
                df["vwap"] = (pv / v).fillna(df["Close"])

            heavy_absorption = (
                (df["delivery_pct"] > 50.0) & (df["Close"] > df["vwap"])
            ).astype(float)

            df["delivery_accumulation_signal"] = df["delivery_pct"] * heavy_absorption

            df["ias"] = 0.8 * df["delivery_divergence_score"] + 0.2 * (
                df["delivery_accumulation_signal"] / 100.0
            )

            df["ias"] *= df["vcp"]

        except Exception:
            df["ias"] = 0.0

        # SMC block
        #
        # The import sits *outside* the try on purpose. ``myra_app.engine`` has
        # never defined or re-exported ``SMCManager``, so the previous
        # ``from myra_app.engine import SMCManager`` raised ImportError on every
        # call, and the blanket handler below turned that into
        # ``fvg = 0.0 / bos = False / choch = False``. Every lake artifact
        # therefore carried zeroed SMC indicators that were indistinguishable
        # from measured ones. The class lives in ``myra_app.smc_manager`` (which
        # is where ``worker_pool`` already imports it from). Keeping the import
        # outside the try means a misnamed or missing module is now a loud,
        # countable build failure rather than a silent fabricated value.
        from myra_app.smc_manager import SMCManager

        try:
            df_lower = df.rename(columns=lambda x: x.lower())
            df["fvg"] = SMCManager.calculate_fvg(df_lower)
            bos, choch = SMCManager.calculate_market_structure(df_lower)

            df["bos"] = bos
            df["choch"] = choch

        except Exception:
            # A genuine per-symbol SMC failure must not abort the build, but it
            # must be visible rather than silently published as a zero.
            logger.warning("[MYRA] SMC indicators fell back to zero for %s", sym)
            df["fvg"] = 0.0
            df["bos"] = False
            df["choch"] = False

        # Normalise for persistence (lower-cased columns + symbol tag), so the
        # caller only has to write the frame out.
        df.columns = [c.lower() for c in df.columns]

        # Lower-casing is what *creates* the duplicate columns.
        #
        # ``get_ohlcv`` already returns some lower-case, already-enriched
        # columns (``delivery_pct``, ``vwap``, ``delivery_divergence_score``).
        # The ``capitalize()`` pass above renames them to ``Delivery_pct`` /
        # ``Vwap`` / ``Delivery_divergence_score``, so this method's own
        # assignments to ``delivery_pct`` (below), ``vwap`` and
        # ``delivery_divergence_score`` append a *second* column instead of
        # overwriting the first. Lower-casing both here then produced a frame
        # with two columns of each name, and ``to_parquet`` rejects that with
        # ``ValueError: Duplicate column names found`` — so *every* artifact
        # write failed and ``data/indicators/precomputed`` could never be
        # populated at all (``build_indicator_lake`` reported every symbol as
        # ``failed``, honestly but permanently).
        #
        # ``keep="first"`` (pandas' default, and the same idiom the
        # ``capitalize()`` pass at the top of this method already uses) keeps
        # the ingested/enriched value and drops this method's duplicate. That
        # matters for ``delivery_pct``: ``Delivery_qty`` is absent from
        # ``technical_data`` and only ~7% populated in the OHLCV cache, so the
        # duplicate computed here is ``fillna(0.0)`` — a fabricated zero — while
        # the kept column is the measured NSE delivery percentage. Which of the
        # two competing sources should win is recorded as an open definition
        # question in ``docs/V25_TECHNICAL_SIGNAL_CONTRACT.md``; this repair
        # only avoids publishing the fabricated zero and unblocks the write.
        df = df.loc[:, ~df.columns.duplicated(keep="first")]
        df["symbol"] = sym

        return df

    def build_indicator_lake(
        self,
        symbols=None,
        *,
        force=False,
        max_symbols=None,
        reference_date=None,
        progress=False,
        cancel_event=None,
    ):
        """Build or refresh the indicator lake — bounded, idempotent, resumable.

        This is the *only* supported way to populate ``data/indicators/precomputed``.
        It is deliberately an explicit operator action: nothing on a read path
        calls it, so a scan can never silently kick off a multi-thousand-symbol
        write.

        Why it is safe to re-run:

        * **Freshness gate.** Unless ``force`` is set, a symbol whose artifact is
          already at least as new as ``reference_date`` (by default the newest
          stored market session) is skipped. A completed build therefore costs
          almost nothing to re-run, and an interrupted build simply resumes.
        * **Atomic artifacts.** Each file is written to a temp path and moved
          into place by ``IndicatorManager.save_indicators``, so an interrupted
          run leaves either the old artifact or the new one — never a truncated
          file that loads as if it were valid.
        * **Bounded work.** ``max_symbols`` caps a single invocation, so a build
          can be staged across several runs.
        * **No fabricated defaults.** A symbol with unusable history is counted
          as ``skipped_no_data`` and left absent; it is never written with
          zeroed indicators just to make the count look complete.

        Never raises for a per-symbol failure; the returned summary carries the
        counts and up to 25 concrete error strings.
        """
        from myra_app import indicator_lake as lake

        if getattr(self, "read_only", False):
            return {
                "reason": "read_only",
                "built": 0,
                "skipped_fresh": 0,
                "skipped_no_data": 0,
                "failed": 0,
                "failures": [],
                "cancelled": False,
            }

        if reference_date is None:
            try:
                reference_date = self.get_max_price_date()
            except Exception as exc:  # noqa: BLE001 - gate degrades to "no gate"
                logger.debug("[MYRA] could not resolve reference date: %s", exc)
                reference_date = None

        if symbols is None:
            symbols = self.get_active_universe()
        symbols = list(symbols or [])
        if max_symbols is not None:
            symbols = symbols[:max_symbols]

        summary = {
            "requested": len(symbols),
            "built": 0,
            "skipped_fresh": 0,
            "skipped_no_data": 0,
            "failed": 0,
            "failures": [],
            "reference_date": reference_date,
            "force": bool(force),
            "cancelled": False,
            "elapsed_s": 0.0,
        }
        started = time.time()
        total = len(symbols)

        for i, sym in enumerate(symbols, 1):
            if cancel_event is not None and cancel_event.is_set():
                summary["cancelled"] = True
                break
            if progress:
                myra_log(i, total, desc="Precomputing")

            if not force and reference_date and lake.is_fresh(sym, reference_date):
                summary["skipped_fresh"] += 1
                continue

            try:
                df = self._compute_symbol_indicators(sym)
                if df is None:
                    summary["skipped_no_data"] += 1
                    continue
                self.loader.indicators.save_indicators("precomputed", sym, df)
                summary["built"] += 1
            except Exception as exc:  # noqa: BLE001 - one bad symbol must not abort
                summary["failed"] += 1
                if len(summary["failures"]) < 25:
                    summary["failures"].append(
                        {"symbol": sym, "error": f"{type(exc).__name__}: {exc}"}
                    )
                logger.debug("[MYRA] indicator build failed for %s: %s", sym, exc)

        summary["elapsed_s"] = round(time.time() - started, 2)
        return summary

    def update_indicator_history(self):
        """Rebuild the indicator lake for every active symbol.

        Kept for backwards compatibility with existing callers. Prefer
        ``build_indicator_lake`` (the bounded, freshness-gated form) for anything
        new: this wrapper forces a full, unconditional rebuild.
        """
        if getattr(self, "read_only", False):
            return

        active_symbols = self.get_active_universe()
        if not active_symbols:
            print("[!] No active symbols found for indicator update.")
            return

        print(
            f"[MYRA] Updating Virtual Indicator Lake for {len(active_symbols)} symbols..."
        )
        summary = self.build_indicator_lake(
            active_symbols,
            force=True,
            reference_date=None,
            progress=True,
        )
        print(
            "[+] Indicator Lake Updated "
            f"({summary['built']} built, {summary['skipped_no_data']} without "
            f"history, {summary['failed']} failed in {summary['elapsed_s']}s)."
        )
        if summary["failures"]:
            for failure in summary["failures"][:10]:
                print(f"   • {failure['symbol']}: {failure['error']}")
        return summary
