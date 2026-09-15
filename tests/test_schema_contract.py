"""Schema-contract smoke tests.

Verify that the synthetic fixture DBs (tests/fixtures/*_fixture.db) match the
ground-truth schema dump in schema/schema_manifest.json for the 9 registered
DB_MAP databases. Catches drift between the fixture builder and real DB DDL.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

import myra_app.constants as constants
from myra_app.librarian_core import LibrarianCore
from tests.fixtures.build_fixture_db import NO_SEED

REGISTERED_DBS = tuple(LibrarianCore.DB_MAP)

# Internal SQLite tables auto-created (e.g. sqlite_sequence for AUTOINCREMENT)
# are not part of the manifest and are excluded from the comparison.
_SYSTEM_TABLES = {"sqlite_sequence", "sqlite_stat1"}


@pytest.fixture(scope="module", autouse=True)
def _manifest():
    manifest_path = os.path.join(
        constants.PROJECT_ROOT, "schema", "schema_manifest.json"
    )
    if not os.path.exists(manifest_path):
        pytest.skip("missing schema/schema_manifest.json (run scripts/dump_schema.py)")
    with open(manifest_path, encoding="utf-8") as fh:
        return json.load(fh)


def _db_tables(db_path: str) -> dict[str, set[str]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        } - _SYSTEM_TABLES

        result = {}
        for name in sorted(tables):
            cols = {
                (row[1], row[2])
                for row in conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            }
            result[name] = cols
    finally:
        conn.close()
    return result


@pytest.mark.usefixtures("fixture_db_dir")
@pytest.mark.parametrize("db_key", REGISTERED_DBS)
def test_fixture_matches_manifest(db_key, fixture_db_dir, _manifest):
    db_entry = _manifest[db_key]
    db_path = os.path.join(fixture_db_dir, LibrarianCore.DB_MAP[db_key])
    assert os.path.exists(db_path), f"missing fixture DB for {db_key}: {db_path}"

    manifest_tables = {t["name"] for t in db_entry["tables"]}
    fixture_tables = _db_tables(db_path)
    fixture_table_names = set(fixture_tables)

    assert fixture_table_names == manifest_tables, (
        f"[{db_key}] table sets differ. "
        f"fixture-only={sorted(fixture_table_names - manifest_tables)} "
        f"manifest-only={sorted(manifest_tables - fixture_table_names)}"
    )

    for entry in db_entry["tables"]:
        manifest_cols = {(c["name"], c["type"]) for c in entry["columns"]}
        fixture_cols = fixture_tables[entry["name"]]
        assert fixture_cols == manifest_cols, (
            f"[{db_key}].{entry['name']} columns differ. "
            f"fixture-only={sorted(fixture_cols - manifest_cols)} "
            f"manifest-only={sorted(manifest_cols - fixture_cols)}"
        )


@pytest.mark.usefixtures("fixture_db_dir")
@pytest.mark.parametrize("db_key", REGISTERED_DBS)
def test_fixture_has_seed_rows(db_key, fixture_db_dir):
    db_path = os.path.join(fixture_db_dir, LibrarianCore.DB_MAP[db_key])
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        } - _SYSTEM_TABLES
        skip = NO_SEED.get(db_key, set())
        for name in sorted(tables):
            count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            if name in skip:
                continue
            assert count > 0, f"[{db_key}].{name} expected >0 seeded rows in fixture"
    finally:
        conn.close()
