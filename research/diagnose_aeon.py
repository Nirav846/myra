import os
import sys
import numpy as np
import pandas as pd
import joblib

# Add current dir to path
sys.path.append(os.getcwd())

from myra_app.librarian import Librarian
from myra_app.ml_engine import EvolutionaryAgent, SMCEnvironment


def diagnose():
    lib = Librarian(read_only=True)
    symbols = lib.get_index_symbols("NIFTY 50")
    if not symbols:
        return

    df = lib.conn.execute(
        f"SELECT * FROM calculated_indicators WHERE symbol = '{symbols[0]}' ORDER BY date ASC"
    ).df()
    if df.empty:
        return

    agent = EvolutionaryAgent(input_size=480, hidden_size=16, output_size=4)
    model_path = "models/aeon_agent.joblib"
    if os.path.exists(model_path):
        genes = joblib.load(model_path)
        agent.set_genes(genes)
        print(f"Loaded model from {model_path}")

    env = SMCEnvironment(df)
    states = env.get_all_states()

    if len(states) == 0:
        print("No states found.")
        return

    print(f"Total states: {len(states)}")

    # Check first state
    s0 = states[0:1]
    probs = agent.get_probs(s0)
    print(f"Probs for first state: {probs}")
    action = agent.forward(s0)
    print(f"Action for first state: {action}")

    # Check all actions
    all_actions = agent.forward(states)
    unique, counts = np.unique(all_actions, return_counts=True)
    print(f"Action distribution: {dict(zip(unique, counts))}")

    # Check if inputs are all zero
    print(f"Mean of all states: {np.mean(states)}")
    print(f"Std of all states: {np.std(states)}")
    print(f"Max of all states: {np.max(states)}")
    print(f"Min of all states: {np.min(states)}")

    lib.close()


if __name__ == "__main__":
    diagnose()
