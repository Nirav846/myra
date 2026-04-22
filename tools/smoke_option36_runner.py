import sys
import traceback
from smoke_option36 import Dummy

out_path = 'tools/smoke_option36_out.txt'
with open(out_path, 'w') as f:
    try:
        d = Dummy()
        d.update_indicator_history()
        f.write('Smoke run completed without exceptions\n')
    except Exception:
        f.write('Smoke run FAILED:\n')
        traceback.print_exc(file=f)
print('runner executed, check', out_path)
