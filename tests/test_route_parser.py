import os
import subprocess
import sqlite3
import sys
import glob
import pytest

ROUTE_PARSER = os.path.join("c_cxx_source_parser", "route_parser.py")
HEADER_DB = "tests/definitions_test.db"
IMPL_DB = "tests/implementations_test.db"
TARGET_DIR = "tests/test_targets"

def remove_db(path):
    if os.path.exists(path):
        os.remove(path)

def run_route_parser(file, db):
    cmd = [sys.executable, ROUTE_PARSER, file, "-db", db]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result

def check_db_tables(db_path, required_tables):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = set(row[0] for row in cur.fetchall())
    conn.close()
    return all(t in tables for t in required_tables)

def test_header_file_routing():
    remove_db(HEADER_DB)
    header_files = glob.glob(os.path.join(TARGET_DIR, "*.h"))[0]
    result = run_route_parser(header_files, HEADER_DB)
    assert result.returncode == 0
    assert os.path.exists(HEADER_DB)
    assert check_db_tables(HEADER_DB, ["files", "macros", "functions", "structs_unions", "enums", "typedefs", "variables"])

def test_impl_file_routing():
    remove_db(IMPL_DB)
    impl_files = glob.glob(os.path.join(TARGET_DIR, "*.c"))[0]
    result = run_route_parser(impl_files, IMPL_DB)
    assert result.returncode == 0
    assert os.path.exists(IMPL_DB)
    assert check_db_tables(IMPL_DB, ["files", "macros", "functions", "structs_unions", "enums", "typedefs", "variables"])

def test_file_routing():
    remove_db(HEADER_DB)
    remove_db(IMPL_DB)
    source_files = glob.glob(os.path.join(TARGET_DIR, "*.*"))
    for f in source_files:
        if f.endswith(".h"):
            db_path = HEADER_DB
        elif f.endswith(".c"):
            db_path = IMPL_DB
        else:
            continue
        result = run_route_parser(ROUTE_PARSER, f, db_path)
        assert result.returncode == 0
        assert os.path.exists(db_path)
        assert check_db_tables(db_path, ["files", "macros", "functions", "structs_unions", "enums", "typedefs", "variables"])

def test_unsupported_file_type():
    fake_file = "tests/test_targets/fake.txt"
    with open(fake_file, "w") as f:
        f.write("dummy")
    result = run_route_parser(fake_file, "tests/fake.db")
    os.remove(fake_file)
    assert result.returncode != 0
    assert "unsupported" in result.stderr.lower() or "unsupported" in result.stdout.lower()

def test_missing_file():
    missing_file = "tests/test_targets/missing_file.h"
    result = run_route_parser(missing_file, HEADER_DB)
    assert result.returncode != 0
    assert "not found" in result.stderr.lower() or "not found" in result.stdout.lower()

if __name__ == "__main__":
    pytest.main([__file__])
