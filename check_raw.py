import sys; sys.path.insert(0, '.')
from myra_app.librarian import Librarian
from myra_app.worker_pool import _worker_task, init_worker
lib = Librarian(read_only=True)
lib.connect()
init_worker('2', lib.db_path)
funda = {'symbol': 'HDFCBANK'}
result = _worker_task(('HDFCBANK', '2', None, funda))
print('Worker result:', result)
