import os
import shutil

import pytest

import myra_app.constants as constants
from myra_app.librarian_core import LibrarianCore


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow")


@pytest.fixture(scope="session")
def fixture_db_dir(tmp_path_factory):
    """Copy fixture DBs to a tmp dir and point myra_app.constants.DB_DIR there.

    Opt-in (non-autouse): tests that request this fixture run against the
    synthetic fixtures; everything else keeps the real DB_DIR. Session-scoped
    so the copy + monkeypatch happen once per test run.
    """
    fixtures_src = os.path.join(constants.PROJECT_ROOT, "tests", "fixtures")
    dest = tmp_path_factory.mktemp("myra_fixture_dbs")

    for db_key, filename in LibrarianCore.DB_MAP.items():
        src = os.path.join(fixtures_src, f"{db_key}_fixture.db")
        if not os.path.exists(src):
            pytest.skip(
                f"missing fixture DB (run tests/fixtures/build_fixture_db.py): {src}"
            )
        shutil.copy2(src, os.path.join(str(dest), filename))

    orig_db_dir = constants.DB_DIR
    constants.DB_DIR = str(dest)
    try:
        yield str(dest)
    finally:
        constants.DB_DIR = orig_db_dir
