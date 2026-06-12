import sys
sys.path.insert(0, r'D:\01screener\Myra')
from myra_app.utils.bse_shareholding import run_backfill
run_backfill(max_symbols=None)
