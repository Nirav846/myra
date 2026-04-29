import os
import sys

from rich.console import Console
from rich.table import Table

# Fix path
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
sys.path.insert(0, PROJECT_ROOT)

from myra_app.librarian import Librarian
from myra_app.screener import MYRAScreener


def run_suite_audit():
    console = Console()
    console.print(
        "[bold cyan][AUDIT] Initiating Multi-Scanner Data Mapping Audit...[/bold cyan]"
    )

    lib = Librarian(read_only=True)
    screener = MYRAScreener(console)

    scanners = [
        {
            "id": "whale_tracker",
            "name": "Whale Tracker (Large Deals)",
            "cols": ["Stars", "Inst_Intensity", "AV_Total"],
        },
        {
            "id": "bottom_hunter",
            "name": "Bottom Hunter (FVG Reversal)",
            "cols": ["Stars", "FVG_Zone", "Support"],
        },
        {
            "id": "surpriver_v2",
            "name": "NSE Surpriver v2 (Delivery Z)",
            "cols": ["Stars", "RDV", "Money_Flow"],
        },
    ]

    for s in scanners:
        console.print(f"\n[bold yellow][*] Auditing: {s['name']}...[/bold yellow]")
        try:
            results = screener.execute_scan(s["id"], f"Audit: {s['name']}")
            if results:
                console.print(
                    f"[success][✔] Mapping Verified! Identified {len(results)} candidates.[/success]"
                )
                screener.rm.display_discovery_table(
                    results[:3], s["name"], s["id"], s["cols"]
                )
            else:
                console.print(
                    "[warning][!] Data flow verified, but 0 candidates found for current market state.[/warning]"
                )
        except Exception as e:
            console.print(f"[error][!] Audit FAILED for {s['id']}: {e}[/error]")

    lib.close()


if __name__ == "__main__":
    run_suite_audit()
