from rich.console import Console
from rich.panel import Panel

class MenuNavigator:
    """
    MYRA Menu Navigator (v1.0)
    Handles complex sub-menu navigation, breadcrumbs, and shortcut injection.
    """
    def __init__(self, console: Console):
        self.console = console
        self.breadcrumb = []

    def push(self, name: str):
        """Adds a segment to the navigation path."""
        self.breadcrumb.append(name)

    def pop(self):
        """Removes the last segment from the navigation path."""
        if self.breadcrumb:
            self.breadcrumb.pop()

    def show_path(self):
        """Displays the current navigation breadcrumb."""
        path_str = " > ".join(self.breadcrumb)
        if path_str:
            self.console.print(f"[dim]Location: {path_str}[/dim]")

    def clear_screen(self):
        """Clears the terminal for a fresh menu state."""
        self.console.clear()

    def render_menu(self, title: str, options: list):
        """Renders a standard MYRA sub-menu."""
        self.show_path()
        self.console.print(f"\n[bold cyan]{title}:[/bold cyan]")
        for opt in options:
            self.console.print(f"  {opt}")
        
        return self.console.input("\n[bold yellow]Select Option > [/bold yellow]").upper()

    def handle_shortcut(self, shortcut_str: str):
        """
        Parses a startup shortcut (e.g., '26:1' for Institutional Turnaround).
        Returns (choice, sub_choice)
        """
        if ":" in shortcut_str:
            parts = shortcut_str.split(":")
            return parts[0], parts[1]
        return shortcut_str, None
