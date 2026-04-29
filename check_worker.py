import sys; sys.path.insert(0, '.')
from myra_app.librarian import Librarian
from myra_app.engine import Engine
lib = Librarian(read_only=True)
lib.connect()
e = Engine(librarian=lib)
results, _ = e.run_scan(['HDFCBANK'], '2')
print('Valid results:', len(results))
for r in results:
    print(r)
