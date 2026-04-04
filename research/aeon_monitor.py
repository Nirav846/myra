
import os
import sys
import json
import numpy as np
import matplotlib.pyplot as plt
from rich.console import Console
from rich.table import Table

# 2. Implementation: The Absolute Path Anchor
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def monitor():
    console = Console()
    history_path = os.path.join(BASE_DIR, "models", "aeon_fitness.json")
    
    if not os.path.exists(history_path):
        console.print("[error][!] No fitness history found. Run train_aeon.py first.[/error]")
        return
        
    with open(history_path, 'r') as f:
        history = json.load(f)
        
    if not history:
        console.print("[warning][!] Fitness history is empty.[/warning]")
        return
        
    # Display table of last 10 iterations
    table = Table(title="AEON Evolution Progress", header_style="bold cyan", border_style="dim")
    table.add_column("Iteration", justify="right")
    table.add_column("Reward (Fitness)", justify="right")
    
    for entry in history[-10:]:
        table.add_row(str(entry["iteration"]), f"{entry['fitness']:.4f}")
        
    console.print(table)
    
    # Plotting
    iterations = [e["iteration"] for e in history]
    fitness = [e["fitness"] for e in history]
    
    plt.figure(figsize=(10, 6))
    plt.plot(iterations, fitness, marker='o', linestyle='-', color='b')
    plt.title("AEON Evolutionary Fitness over Time")
    plt.xlabel("Iteration")
    plt.ylabel("Reward")
    plt.grid(True)
    
    save_path = os.path.join(BASE_DIR, "myra_reports", "charts", "aeon_evolution.png")
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    plt.savefig(save_path)
    console.print(f"[success][✔] Evolution chart saved to {save_path}[/success]")

if __name__ == "__main__":
    monitor()
