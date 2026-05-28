import sqlite3, time
conn = sqlite3.connect('myra_app/db/myra_technical.db')
ticker = 'RELIANCE'
lookback = '180 days'
start = time.time()
# Run the same query the widget uses
# (We need to see the actual SQL first — paste it from step 1)
conn.close()
