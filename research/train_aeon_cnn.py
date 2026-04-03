
import os
import sys
import numpy as np
import pandas as pd
import duckdb
from datetime import datetime
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

# Add current dir to path
sys.path.append(os.getcwd())

from myra_app.librarian import Librarian

console = Console()

def build_dilated_cnn_model(input_shape, output_size):
    import tensorflow as tf
    from tensorflow.keras.layers import Input, Conv1D, Dense, Dropout, Lambda
    from tensorflow.keras.models import Model

    inputs = Input(shape=input_shape)
    x = Dense(128)(inputs)
    
    # Dilated CNN Blocks
    for i in range(4):
        dilation_rate = 2 ** i
        x = Conv1D(filters=128, kernel_size=3, dilation_rate=dilation_rate, padding='causal', activation='relu')(x)
    
    # Attention-like weighting: Take the last output of the sequence
    x = Lambda(lambda x: x[:, -1, :])(x) 
    
    x = Dropout(0.2)(x)
    outputs = Dense(output_size)(x)
    
    model = Model(inputs, outputs)
    model.compile(optimizer='adam', loss='mse')
    return model

def prepare_data(df, window_size=60):
    # Features: Open, High, Low, Close, Volume, delivery_qty, delivery_percent, rdv
    cols = ['open', 'high', 'low', 'close', 'volume', 'delivery_qty', 'delivery_percent', 'rdv']
    
    # Check if all columns exist
    missing = [c for c in cols if c not in df.columns]
    if missing:
        # Try to find CamelCase variants
        mapping = {'open':'Open', 'high':'High', 'low':'Low', 'close':'Close', 'volume':'Volume'}
        for m in missing:
            if mapping.get(m) in df.columns:
                df[m] = df[mapping[m]]
        
    # Re-check
    missing = [c for c in cols if c not in df.columns]
    if missing: return None, None
    
    data = df[cols].copy()
    
    # Simple normalization: pct_change or relative to current close
    # Here we use pct_change for all except delivery_percent and rdv which are already bounded/ratio
    for c in ['open', 'high', 'low', 'close', 'volume', 'delivery_qty']:
        data[c] = data[c].pct_change().fillna(0)
        
    data['delivery_percent'] = data['delivery_percent'] / 100.0
    data['rdv'] = np.clip(data['rdv'] / 5.0, 0, 2)
    
    values = data.values
    X, y = [], []
    for i in range(window_size, len(values)):
        X.append(values[i-window_size : i])
        # Target: next day return
        y.append(values[i, 3]) # index 3 is 'close' pct_change
        
    if not X: return None, None

    X_arr = np.array(X, dtype=np.float32)
    y_arr = np.array(y, dtype=np.float32)
    
    # Handle NaNs/Infs
    X_arr = np.nan_to_num(X_arr, nan=0.0, posinf=0.0, neginf=0.0)
    y_arr = np.nan_to_num(y_arr, nan=0.0, posinf=0.0, neginf=0.0)
    
    return X_arr, y_arr
def train_cnn():
    console.print("[bold magenta]--- [AEON DILATED CNN TRAINING (ML-2)] ---[/bold magenta]")
    
    lib = Librarian(read_only=True)
    symbols = lib.get_active_universe()[:50] # Sample 50 symbols for training
    
    all_X, all_y = [], []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task("[cyan]Loading Data...", total=len(symbols))
        
        for sym in symbols:
            df = lib.conn.execute(f"SELECT * FROM calculated_indicators WHERE symbol = '{sym}' ORDER BY date ASC").df()
            if len(df) > 100:
                X, y = prepare_data(df)
                if X is not None:
                    all_X.append(X)
                    all_y.append(y)
            progress.update(task, advance=1)
            
    if not all_X:
        console.print("[error][!] No training data found.[/error]")
        return
        
    X_train = np.concatenate(all_X)
    y_train = np.concatenate(all_y)
    
    console.print(f"[*] Training on {X_train.shape[0]} samples...")
    
    model = build_dilated_cnn_model((60, 8), 1)
    
    # Train
    model.fit(X_train, y_train, epochs=10, batch_size=64, validation_split=0.1, verbose=1)
    
    # Save
    model_path = "models/aeon_cnn_forecast.keras"
    os.makedirs("models", exist_ok=True)
    model.save(model_path)
    console.print(f"[success][✔] CNN Model saved to {model_path}[/success]")
    
    lib.close()

if __name__ == "__main__":
    train_cnn()
