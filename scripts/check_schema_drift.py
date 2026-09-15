#!/usr/bin/env python
"""
Pre-commit guard: fail if schema/*.sql / schema/schema_manifest.json drifted
from the live local SQLite sidecars.

Reuses scripts/dump_schema.py's generate_schema_outputs() so the comparison
logic lives in exactly one place. If running dump_schema would change any
committed artifact, the commit is blocked until the artifacts are regenerated
and staged (python scripts/dump_schema.py).

Safety:
  * SNAPSHOT-BASED: DBs are copied to a temp dir before comparison, so the
    guard never opens the live tracked files directly. This prevents
    .db-shm rewrites (SQLite writes WAL index marks even in mode=ro) that
    would trigger "files were modified by this hook" under pre-commit's
    stash/restore cycle.
  * This hook never stages, adds, or writes anything to the repo -- no git
    commands, no DB writes to tracked files. It only compares generated
    strings against the committed files.

Skip behaviour:
  * If DB_DIR does not exist, or none of the registered DB files are present,
    the check exits 0 -- this is the fresh-clone / no-live-DBs case and must
    not block first-time setup.
  * A registered DB that exists but cannot be opened cleanly (malformed /
    locked image) is skipped with a warning, not treated as drift. This keeps
    the hook stable when a DB is mid-write during the snapshot copy.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import myra_app.constants as constants
from myra_app.librarian_core import LibrarianCore
from dump_schema import generate_schema_outputs, UNREGISTERED_DB_FILES

SCHEMA_DIR = os.path.join(constants.PROJECT_ROOT, "schema")


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _table_structure(db_meta: dict) -> list[tuple]:
    """Structural fingerprint of one DB's tables (row counts excluded)."""
    out = []
    for t in db_meta.get("tables", []):
        cols = tuple(
            (
                c.get("name"),
                c.get("type"),
                bool(c.get("notnull")),
                bool(c.get("pk")),
            )
            for c in t.get("columns", [])
        )
        out.append((t["name"], cols))
    return sorted(out, key=lambda x: x[0])


def _snapshot_db_dir(db_dir: str, present: list[str]) -> str | None:
    """Copy registered DB files + their -wal into a temp dir so SQLite opens
    copies instead of the live tracked files.  This prevents the guard from
    modifying .db-shm sidecars (SQLite writes WAL index marks even in
    mode=ro), which would show up as "files were modified by this hook"
    under pre-commit's stash/restore cycle."""
    tmp = tempfile.mkdtemp(prefix="schema_guard_")
    copied_any = False

    for key, filename in LibrarianCore.DB_MAP.items():
        if key not in present:
            continue
        src_db = os.path.join(db_dir, filename)
        shutil.copy2(src_db, os.path.join(tmp, filename))
        copied_any = True

        wal = src_db + "-wal"
        if os.path.exists(wal):
            shutil.copy2(wal, os.path.join(tmp, filename + "-wal"))

        shm = src_db + "-shm"
        if os.path.exists(shm):
            shutil.copy2(shm, os.path.join(tmp, filename + "-shm"))

    for fname in UNREGISTERED_DB_FILES:
        src_db = os.path.join(db_dir, fname)
        if not os.path.exists(src_db):
            continue
        shutil.copy2(src_db, os.path.join(tmp, fname))
        copied_any = True

        wal = src_db + "-wal"
        if os.path.exists(wal):
            shutil.copy2(wal, os.path.join(tmp, fname + "-wal"))

        shm = src_db + "-shm"
        if os.path.exists(shm):
            shutil.copy2(shm, os.path.join(tmp, fname + "-shm"))

    if not copied_any:
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    return tmp


def check() -> int:
    db_dir = constants.DB_DIR
    if not os.path.isdir(db_dir):
        print(f"[schema-drift-guard] DB_DIR not found ({db_dir}); skipping.")
        return 0

    present = [
        key
        for key, filename in LibrarianCore.DB_MAP.items()
        if os.path.exists(os.path.join(db_dir, filename))
    ]
    if not present:
        print("[schema-drift-guard] no live DBs under DB_DIR; skipping.")
        return 0

    snapshot = _snapshot_db_dir(db_dir, present)
    if snapshot is None:
        print("[schema-drift-guard] snapshot copy failed; skipping.")
        return 0

    try:
        files, manifest, _mismatch, _missing, unreadable = generate_schema_outputs(
            snapshot
        )
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)

    if not os.path.isdir(SCHEMA_DIR):
        os.makedirs(SCHEMA_DIR, exist_ok=True)

    for key in unreadable:
        print(
            f"[schema-drift-guard] WARNING: {key} unreadable even in snapshot; "
            f"skipping its drift check."
        )

    # DBs that are present AND readable can be compared. Unreadable ones are
    # skipped (warned above) -- this keeps the hook stable if a DB is
    # actively being written to during the snapshot copy.
    comparable = [key for key in present if key not in unreadable]

    problems: list[str] = []

    for key in comparable:
        rel = f"{key}.sql"
        generated = files.get(rel)
        committed_path = os.path.join(SCHEMA_DIR, rel)
        if generated is None:
            problems.append(f"{rel}: DB present but no generated DDL")
            continue
        if not os.path.exists(committed_path):
            problems.append(f"{rel}: missing from schema/ -- regenerate and stage it")
            continue
        with open(committed_path, encoding="utf-8", newline="") as fh:
            committed = fh.read()
        if _norm(generated) != _norm(committed):
            problems.append(
                f"{rel}: out of date vs live DB -- run python scripts/dump_schema.py"
            )

    manifest_path = os.path.join(SCHEMA_DIR, "schema_manifest.json")
    if not os.path.exists(manifest_path):
        problems.append(
            "schema/schema_manifest.json: missing -- regenerate and stage it"
        )
    else:
        with open(manifest_path, encoding="utf-8", newline="") as fh:
            committed_manifest = json.load(fh)
        for key in comparable:
            if key not in manifest or key not in committed_manifest:
                problems.append(f"{key}: schema_manifest.json entry mismatch")
                continue
            if _table_structure(manifest[key]) != _table_structure(
                committed_manifest[key]
            ):
                problems.append(
                    f"{key}: schema_manifest.json out of date vs live DB "
                    "-- run python scripts/dump_schema.py"
                )

    if problems:
        print("[schema-drift-guard] DRIFT DETECTED:")
        for p in sorted(problems):
            print(f"  - {p}")
        print("\n  Fix: run `python scripts/dump_schema.py`, review the diff,")
        print("  and stage the regenerated schema/ artifacts.")
        return 1

    print("[schema-drift-guard] schema/ matches live DBs.")
    return 0


if __name__ == "__main__":
    sys.exit(check())
