
import os
import duckdb
import numpy as np
import pandas as pd
import joblib
from datetime import datetime
from myra_app.ml_engine import EvolutionaryAgent, SMCEnvironment, DeepEvolutionStrategy
from myra_app.librarian import Librarian

# --- PRODUCTION AEON CONFIG (ES-1) ---
POPULATION_SIZE = 50
ITERATIONS = 50
SIGMA = 0.1
LEARNING_RATE = 0.03
TOP_WINNERS_COUNT = 50 
BOTTOM_LOSERS_COUNT = 50 

def train_es():
    db_path = "results/Data/myra_market_data.db"
    if not os.path.exists(db_path): db_path = "myra.db"
    
    print(f"[*] AEON-ES: Connecting to {db_path}...")
    conn = duckdb.connect(db_path, read_only=True)
    
    # 1. Data Selection
    query_winners = "SELECT symbol FROM index_constituents WHERE index_name = 'NIFTY 500' LIMIT 50" # Simplified for ES test
    symbols = conn.execute(query_winners).df()['symbol'].tolist()
    
    # 2. Load History
    s_list = "','".join(symbols)
    query_data = f"SELECT * FROM calculated_indicators WHERE symbol IN ('{s_list}') ORDER BY date ASC"
    df_all = conn.execute(query_data).df()
    conn.close()

    # 3. Environment Preparation
    precomputed_envs = []
    for sym in symbols:
        df_sym = df_all[df_all['symbol'] == sym]
        if len(df_sym) < 90: continue
        precomputed_envs.append(SMCEnvironment(df_sym))

    # 4. Agent Initialization
    input_size = 480
    agent = EvolutionaryAgent(input_size=input_size)
    
    # Load existing genes if available
    if os.path.exists("models/aeon_agent.joblib"):
        try:
            genes = joblib.load("models/aeon_agent.joblib")
            if len(genes) == 480 * 16 + 16 + 16 * 4 + 4:
                agent.set_genes(genes)
                print("[*] AEON-ES: Loaded existing model.")
        except: pass

    # 5. Reward Function for ES
    def reward_function(weights_list):
        # Temporarily set agent weights
        keys = sorted(agent.weights.keys())
        for i, k in enumerate(keys):
            agent.weights[k] = weights_list[i]
        
        total_fitness = 0
        for env in precomputed_envs:
            # We use vectorized evaluation for speed
            total_fitness += env.evaluate_agent_vectorized(agent)
        return total_fitness

    # 6. Initialize Deep Evolution Strategy
    initial_weights = [agent.weights[k] for k in sorted(agent.weights.keys())]
    es = DeepEvolutionStrategy(
        initial_weights, 
        reward_function, 
        population_size=POPULATION_SIZE, 
        sigma=SIGMA, 
        learning_rate=LEARNING_RATE
    )

    print(f"[*] AEON-ES: Starting Optimization ({ITERATIONS} iterations)...")
    try:
        final_weights = es.train(iterations=ITERATIONS, print_every=5)
        
        # Save Final Genes
        # Flatten weights back to genes
        gene_list = []
        for w in final_weights:
            gene_list.append(w.flatten())
        final_genes = np.concatenate(gene_list)
        joblib.dump(final_genes, "models/aeon_agent.joblib")
        print("\n[✔] AEON-ES Training Complete. Genes saved.")
        
    except KeyboardInterrupt:
        print("\n[!] Training paused.")

if __name__ == "__main__":
    train_es()
