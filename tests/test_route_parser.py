import os
import subprocess
import sqlite3
import sys
import glob

ROUTE_PARSER = os.path.join("c_cxx_source_parser", "route_parser.py")
HEADER_DB = "tests/definitions_test.db"
IMPL_DB = "tests/implementations_test.db"
TARGET_DIR = "tests/test_targets"
REQUIRED_TABLES = ["files", "macros", "functions", "structs_unions", "enums", "typedefs", "variables"]

def remove_db(path):
    if os.path.exists(path):
        os.remove(path)

def run_route_parser(file, db):
    cmd = [sys.executable, ROUTE_PARSER, file, "-db", db]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result

def check_db_tables(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = set(row[0] for row in cur.fetchall())
    conn.close()
    return all(t in tables for t in REQUIRED_TABLES)

def check_file_entry(db_path, filepath):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT id FROM files WHERE filepath = ?", (os.path.abspath(filepath),))
    row = cur.fetchone()
    conn.close()
    return row is not None

def check_definitions(db_path, file_id):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    for table in REQUIRED_TABLES[1:]:
        cur.execute(f"SELECT 1 FROM {table} WHERE file_id = ?", (file_id,))
        if cur.fetchone():
            conn.close()
            return True
    conn.close()
    return False

def test_all_headers():
    remove_db(HEADER_DB)
    header_files = glob.glob(os.path.join(TARGET_DIR, "*.h"))
    for f in header_files:
        result = run_route_parser(f, HEADER_DB)
        assert result.returncode == 0, f"Failed to parse header: {f}\n{result.stderr}"
    assert os.path.exists(HEADER_DB)
    assert check_db_tables(HEADER_DB)
    for f in header_files:
        assert check_file_entry(HEADER_DB, f), f"Header file not registered in DB: {f}"
        if os.path.getsize(f) > 0:
            conn = sqlite3.connect(HEADER_DB)
            cur = conn.cursor()
            cur.execute("SELECT id FROM files WHERE filepath = ?", (os.path.abspath(f),))
            row = cur.fetchone()
            if row:
                assert check_definitions(HEADER_DB, row[0]), f"No definitions found for header file: {f}"
            conn.close()

def test_all_impls():
    remove_db(IMPL_DB)
    impl_files = glob.glob(os.path.join(TARGET_DIR, "*.c"))
    for f in impl_files:
        result = run_route_parser(f, IMPL_DB)
        assert result.returncode == 0, f"Failed to parse implementation: {f}\n{result.stderr}"
    assert os.path.exists(IMPL_DB)
    assert check_db_tables(IMPL_DB)
    for f in impl_files:
        assert check_file_entry(IMPL_DB, f), f"Implementation file not registered in DB: {f}"
        if os.path.getsize(f) > 0:
            conn = sqlite3.connect(IMPL_DB)
            cur = conn.cursor()
            cur.execute("SELECT id FROM files WHERE filepath = ?", (os.path.abspath(f),))
            row = cur.fetchone()
            if row:
                assert check_definitions(IMPL_DB, row[0]), f"No definitions found for implementation file: {f}"
            conn.close()

def main():
    try:
        test_all_headers()
        print("All header files parsed and checked successfully.")
        test_all_impls()
        print("All implementation files parsed and checked successfully.")
        print("Route parser integration test complete.")
    except AssertionError as e:
        print(f"Test failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
