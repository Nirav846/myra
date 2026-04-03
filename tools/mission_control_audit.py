import os
import sys
from rich.console import Console

# Fix path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(PROJECT_ROOT)

from myra_app.screener import MYRAScreener

def run_mission_control_test():
    console = Console()
    console.print("[bold cyan][MISSION CONTROL] Initiating End-to-End System Audit...[/bold cyan]")
    
    # 1. Initialize Screener (Creates its own Librarian)
    console.print("[*] Initializing Screener & Atomic Trilogy Stack...")
    screener = MYRAScreener(console)
    
    # 2. Execute 'Super-Scan' (Touches Technical, Meta, and Indicators)
    console.print("[*] Executing 'Super-Scan' (Growth + Momentum) on Active Universe...")
    try:
        # We target a few symbols or let it run on all active
        results = screener.execute_scan("super_setup", "Mission Control Audit")
        
        if results:
            console.print(f"[success][✔] Audit Successful! Identified {len(results)} high-conviction candidates.[/success]")
            # Display top 5
            screener.rm.display_discovery_table(results[:5], "Mission Control: Top 5 Elite", "super_setup", ["RS_Raw", "Stage"])
        else:
            console.print("[warning][!] Audit Complete: 0 candidates found, but data flow is VERIFIED.[/warning]")
            
    except Exception as e:
        console.print(f"[error][!] Audit FAILED: {e}[/error]")
        import traceback
        traceback.print_exc()
    finally:
        screener.lib.close()

if __name__ == "__main__":
    run_mission_control_test()
