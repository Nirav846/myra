from rich.panel import Panel
from rich.box import DOUBLE
from rich.console import Console
from datetime import datetime
import os

MYRA_LOGO = r"""
  __  __ __     _______            
 |  \/  |\ \   / /  __ \     /\    
 | \  / | \ \_/ /| |__) |   /  \   
 | |\/| |  \   / |  _  /   / /\ \  
 | |  | |   | |  | | \ \  / ____ \ 
 |_|  |_|   |_|  |_|  \_\/_/    \_\
                                   
 [bold magenta]Myra Yield & Research Analytics[/bold magenta] v2.5
"""

def get_logo_panel():
    return Panel(
        MYRA_LOGO,
        border_style="magenta",
        box=DOUBLE,
        padding=(1, 2)
    )

def get_status_footer(librarian):
    stats = librarian.get_db_stats()
    status = stats.get("status", "Unknown")
    size = stats.get("size", "0MB")
    # Performance Guard Compliant (Fix 30)
    date_str = datetime.now().isoformat(sep=' ', timespec='seconds')
    
    footer_text = f"[bold cyan]DB Status:[/bold cyan] {status} ({size}) | [bold cyan]System Date:[/bold cyan] {date_str}"
    return Panel(footer_text, border_style="blue", box=DOUBLE)

def get_categorized_menu(strategies: dict):
    """Groups strategies into logical categories."""
    cats = {
        "Technicals": ["1", "2", "3", "4", "5", "6", "7", "28", "29"],
        "Institutional": ["16", "23", "25"],
        "Experimental / ML": ["14", "15", "24"],
        "Value / Fundamentals": ["8", "9", "12", "13"]
    }
    
    # Optimized with list comprehension (Fix 50: Avoid .append in loop and chained indexing)
    categorized = {}
    for cat_name, ids in cats.items():
        def _get_name(sid):
            entry = strategies[sid]
            return entry[1]
            
        categorized[cat_name] = [
            (s_id, _get_name(s_id)) 
            for s_id in ids 
            if s_id in strategies
        ]
                
    return categorized

def render_categorized_menu(console, categorized_menu):
    """Renders the menu using a Rich Table for side-by-side columns."""
    from rich.table import Table
    from rich.text import Text
    
    table = Table(show_header=False, box=None, padding=(0, 2))
    # Add columns for each category
    for cat in categorized_menu.keys():
        table.add_column(cat, justify="left", style="cyan")
        
    # Find max rows
    max_rows = max(len(opts) for opts in categorized_menu.values())
    
    # Fill rows
    for i in range(max_rows):
        # Optimized with list comprehension (Fix 74, 76: Avoid .append in loop)
        def _get_row_item(cat):
            opts = categorized_menu[cat]
            if i < len(opts):
                s_id, s_name = opts[i]
                return f"[bold yellow]{s_id:>2}[/bold yellow] > {s_name}"
            return ""
            
        row_data = [_get_row_item(cat) for cat in categorized_menu.keys()]
        table.add_row(*row_data)
        
    # Wrap in panels for each category or one big panel
    from rich.console import Group
    group = Group()
    for cat, opts in categorized_menu.items():
        cat_text = Text(f"\n {cat} ", style="bold black on cyan")
        console.print(cat_text)
        for s_id, s_name in opts:
            console.print(f"  [bold yellow]{s_id:>2}[/bold yellow] > {s_name}")
            
    # Actually, side-by-side table is cleaner
    console.print(Panel(table, title="[bold white]Main Menu Options[/bold white]", border_style="blue"))
