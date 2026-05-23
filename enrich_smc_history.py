import sqlite3, os, time
from myra_app.constants import DB_DIR
from myra_app.librarian_core import LibrarianCore
from myra_app.utils.smc_calculator import calculate_smc_indicators
import polars as pl

tech = os.path.join(DB_DIR, LibrarianCore.DB_MAP['technical'])
conn = sqlite3.connect(tech)

dates = sorted([r[0] for r in conn.execute('SELECT DISTINCT date FROM technical_data ORDER BY date').fetchall()])
chunk_size = 60

for start in range(0, len(dates), chunk_size):
    chunk = dates[start:start+chunk_size]
    print(f'Processing {chunk[0]} to {chunk[-1]} ({start+1}-{min(start+chunk_size, len(dates))} of {len(dates)})')

    load_start = dates[max(0, start-120)]
    query = "SELECT * FROM technical_data WHERE date >= ? AND date <= ? ORDER BY symbol, date"
    df = pl.read_database(
        query,
        conn,
        infer_schema_length=None,
        schema_overrides={'volume': pl.Int64, 'delivery': pl.Int64},
        execute_options={"parameters": [load_start, chunk[-1]]}
    )

    pdf = df.to_pandas().rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'})
    smc = calculate_smc_indicators(pdf)
    smc_pl = pl.from_pandas(smc)

    for col in ['bullish_fvg','bearish_fvg','fvg_top','fvg_bottom','fvg_boundary','fvg_freshness',
                 'swing_high','swing_low','liquidity_distance','has_bullish_fvg',
                 'trend_alignment','delivery_ma_60']:
        if col in smc_pl.columns:
            for row in smc_pl.filter(pl.col('date').is_in(chunk)).to_dicts():
                val = row[col]
                if val is not None:
                    conn.execute(f'UPDATE technical_data SET {col} = ? WHERE symbol = ? AND date = ?',
                                 (float(val), row['symbol'], str(row['date'])))
    conn.commit()
    print(f'  Chunk done')

conn.close()
print('Historical SMC enrichment complete')
