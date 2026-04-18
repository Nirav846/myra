#!/usr/bin/env python
"""
MYRA Logic Guardian - SQLancer-Inspired Oracle Testing (TRILOGY ERA)
Prevents logic bugs in Turbo-SQL and Librarian data partitioning.
Implements NoREC and TLP patterns for mathematical integrity.
"""
import os
import sqlite3
import pandas as pd
import sqlite3
from myra_app.librarian import Librarian


class LogicGuardian:
    def __init__(self):
        self.lib = Librarian()
        self.results = {"passed": 0, "failed": 0, "errors": []}

    ### 1. NoREC Audit (Non-optimizing Reference Engine Check) ###
    def audit_norec_query(self, table_name, predicate):
        """
        Verifies if SQLite's optimizer matches a brute-force Python filter.
        Logic: Optimized SQL Count == Brute-Force DataFrame Count.
        """
        try:
            # 1. Get Optimized Result (SQLite)
            sql = f"SELECT symbol FROM {table_name} WHERE {predicate}"
            df_sql = pd.read_sql(sql, self.lib._tech_conn)
            sql_count = len(df_sql)

            # 2. Get Reference Result (Brute-Force Pandas)
            # We fetch ALL data for that table and filter manually
            df_raw = pd.read_sql(f"SELECT * FROM {table_name}", self.lib._tech_conn)

            # Translate SQL predicate to Pandas
            # Using query() which is close to SQL syntax
            df_ref = df_raw.query(
                predicate.replace("=", "==").replace("AND", "&").replace("OR", "|")
            )
            ref_count = len(df_ref)

            # 3. Validate
            if sql_count == ref_count:
                self.results["passed"] += 1
                return True, f"NoREC Passed: {sql_count} rows match."
            else:
                msg = f"NoREC FAILED: Optimized({sql_count}) vs Brute({ref_count})."
                self.results["failed"] += 1
                self.results["errors"].append(msg)
                return False, msg

        except Exception as e:
            self.results["errors"].append(f"NoREC Error: {e}")
            return False, str(e)

    ### 2. TLP Audit (Ternary Logic Partitioning) ###
    def audit_tlp_partitioning(self, table_name, predicate):
        """
        Verifies if a table is correctly partitioned into TRUE, FALSE, and NULL.
        Logic: Total == Count(P) + Count(NOT P) + Count(P IS NULL).
        """
        try:
            total = self.lib._tech_conn.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0]

            # SQLite Query
            p_true = self.lib._tech_conn.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE ({predicate})"
            ).fetchone()[0]
            p_false = self.lib._tech_conn.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE NOT ({predicate})"
            ).fetchone()[0]
            p_null = self.lib._tech_conn.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE ({predicate}) IS NULL"
            ).fetchone()[0]

            partition_sum = p_true + p_false + p_null

            if total == partition_sum:
                self.results["passed"] += 1
                return True, f"TLP Passed: {total} == {p_true} + {p_false} + {p_null}"
            else:
                msg = f"TLP FAILED: Total({total}) != PartitionSum({partition_sum})."
                self.results["failed"] += 1
                self.results["errors"].append(msg)
                return False, msg

        except Exception as e:
            self.results["errors"].append(f"TLP Error: {e}")
            return False, str(e)

    def run_full_guardian_audit(self):
        print("\n🛡️ [MYRA] Initializing Logic Guardian Audit...")

        # Audit 1: TLP on Prices (Data integrity)
        print("[*] Testing TLP on 'technical_data' table...")
        res, msg = self.audit_tlp_partitioning("technical_data", "close > 500")
        print(f"    {msg}")

        # Audit 2: NoREC on Price Threshold
        print("[*] Testing NoREC on price threshold...")
        res, msg = self.audit_norec_query("technical_data", "close > 1000")
        print(f"    {msg}")

        print(
            f"\n[+] Audit Complete. Passed: {self.results['passed']} | Failed: {self.results['failed']}"
        )
        if self.results["errors"]:
            print("[!] Critical Logic Failures:")
            for err in self.results["errors"]:
                print(f"    - {err}")


if __name__ == "__main__":
    guardian = LogicGuardian()
    guardian.run_full_guardian_audit()
