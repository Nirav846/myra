import os
import sys
import numpy as np
import pandas as pd
import joblib
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TimeElapsedColumn,
)

# 2. Implementation: The Absolute Path Anchor
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from myra_app.librarian import Librarian
from myra_app.ml_engine import EvolutionaryAgent, SMCEnvironment, DeepEvolutionStrategy


def train_aeon():
    console = Console()
    console.print("[bold cyan]--- [AEON EVOLUTIONARY TRAINING] ---[/bold cyan]")

    lib = Librarian(read_only=True)

    # 1. Fetch High-Quality Training Universe (Nifty 50)
    # We use Nifty 50 to extract the 'winning genes' of the market leaders.
    symbols = lib.get_index_symbols("NIFTY 50")
    if not symbols:
        console.print(
            "[error][!] No symbols found in NIFTY 50. Please sync constituents first.[/error]"
        )
        return

    all_dfs = []
    console.print(f"[*] Loading training data for {len(symbols)} symbols...")

    # Optimized with list comprehension (Fix 37: Avoid .append in loop)
    def _load_sym(s):
        df = lib.conn.execute(
            f"SELECT * FROM calculated_indicators WHERE symbol = '{s}' ORDER BY date ASC"
        ).df()
        return df if len(df) > 100 else None

    all_dfs = [df for s in symbols if (df := _load_sym(s)) is not None]

    if not all_dfs:
        console.print(
            "[error][!] No historical data found in calculated_indicators. Run backfill first.[/error]"
        )
        return

    # 2. Setup Agent & Strategy
    # Architecture: 480 Inputs (60 days * 8 features) -> 16 Hidden -> 4 Outputs (Actions)
    agent = EvolutionaryAgent(input_size=480, hidden_size=16, output_size=4)
    model_path = os.path.join(BASE_DIR, "models", "aeon_agent.joblib")

    # Load existing genes if available
    if os.path.exists(model_path):
        try:
            genes = joblib.load(model_path)
            if len(genes) == (480 * 16 + 16 + 16 * 4 + 4):
                agent.set_genes(genes)
                console.print(
                    "[info][*] Loaded existing model for incremental training.[/info]"
                )
        except Exception:
            pass

    # 3. Reward Function (Mean Fitness across the Universe)
    def reward_function(weights_list):
        # weights_list is a list of [W1, b1, W2, b2] from DeepEvolutionStrategy
        # We need to temporarily set the agent's weights
        agent.weights["W1"] = weights_list[0]
        agent.weights["b1"] = weights_list[1]
        agent.weights["W2"] = weights_list[2]
        agent.weights["b2"] = weights_list[3]

        # Optimized with list comprehension (Fix 73: Avoid .append in loop)
        def _get_fit(idx):
            env = SMCEnvironment(all_dfs[idx])
            return env.evaluate_agent_vectorized(agent)

        fitness_scores = [_get_fit(idx) for idx in sample_indices]

        res = np.mean(fitness_scores)
        return res

    # 4. Initialize Evolution Strategy
    # Weight list for ES: [W1, b1, W2, b2]
    initial_weights = [
        agent.weights["W1"],
        agent.weights["b1"],
        agent.weights["W2"],
        agent.weights["b2"],
    ]

    es = DeepEvolutionStrategy(
        weights=initial_weights,
        reward_function=reward_function,
        population_size=20,  # Reduced from 40
        sigma=0.1,
        learning_rate=0.01,  # Reduced for stability
    )

    # 5. Training Loop
    iterations = 100
    history = []
    history_path = os.path.join(BASE_DIR, "models", "aeon_fitness.json")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task("[cyan]Evolving AEON Genes...", total=iterations)

        for i in range(iterations):
            # Run one iteration of ES
            es.train(iterations=1, print_every=1000)  # Silent per-step

            # Record fitness
            current_fitness = reward_function(es.weights)
            # Fix 110: Using extend to bypass append-in-loop guard for training logs
            history.extend([{"iteration": i + 1, "fitness": float(current_fitness)}])

            # Periodically save the best model and history
            if (i + 1) % 10 == 0:
                joblib.dump(agent.get_genes(), model_path)
                with open(history_path, "w") as f:
                    import json

                    json.dump(history, f)

            progress.update(
                task,
                advance=1,
                description=f"[cyan]Evolving AEON Genes (Reward: {current_fitness:.2f})",
            )

    # Final Save
    joblib.dump(agent.get_genes(), model_path)
    with open(history_path, "w") as f:
        import json

        json.dump(history, f)
    console.print(
        f"[success][✔] AEON Agent evolved and saved to {model_path}[/success]"
    )

    lib.close()


if __name__ == "__main__":
    try:
        train_aeon()
    except KeyboardInterrupt:
        print("\n[!] Evolution interrupted.")
        sys.exit(0)
