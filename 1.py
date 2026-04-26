import os

paths = [
    "myra_technical.db",
    "technical.db",
    "db/myra_technical.db",
    "db/technical.db"
]

for p in paths:
    if os.path.exists(p):
        size = os.path.getsize(p) / (1024*1024)
        print(f"{p} → {size:.2f} MB")
    else:
        print(f"{p} → NOT FOUND")