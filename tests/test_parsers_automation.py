import os
import glob
import subprocess
import sqlite3
import sys

HEADER_DB = "tests/definitions_test.db"
IMPL_DB = "tests/implementations_test.db"
TARGET_DIR = "tests/test_targets"
HEADER_PARSER = "c_cxx_source_parser/header_parser.py"
IMPL_PARSER = "c_cxx_source_parser/impl_parser.py"
REQUIRED_TABLES = [
    "files", "macros", "functions", "structs_unions", "enums", "typedefs", "variables"
]

def remove_db(path):
    if os.path.exists(path):
        os.remove(path)

def run_parser(script, file, db):
    cmd = [sys.executable, script, file, "-db", db]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Error running {script} on {file}:\n{result.stderr}")
            return False
        return True
    except Exception as e:
        print(f"Exception running {script} on {file}: {e}")
        return False

def check_tables(db_path):
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = set(row[0] for row in cur.fetchall())
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        conn.close()
        return missing
    except Exception as e:
        print(f"Error checking tables in {db_path}: {e}")
        return REQUIRED_TABLES

def check_file_entry(db_path, filepath):
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT id FROM files WHERE filepath = ?", (os.path.abspath(filepath),))
        row = cur.fetchone()
        conn.close()
        return row is not None
    except Exception as e:
        print(f"Error checking file entry for {filepath} in {db_path}: {e}")
        return False

def check_definitions(db_path, file_id):
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        for table in REQUIRED_TABLES[1:]:
            cur.execute(f"SELECT 1 FROM {table} WHERE file_id = ?", (file_id,))
            if cur.fetchone():
                conn.close()
                return True
        conn.close()
        return False
    except Exception as e:
        print(f"Error checking definitions for file_id {file_id} in {db_path}: {e}")
        return False

def main():
    # Cleanup
    remove_db(HEADER_DB)
    remove_db(IMPL_DB)

    # Discover files
    header_files = glob.glob(os.path.join(TARGET_DIR, "*.h"))
    impl_files = glob.glob(os.path.join(TARGET_DIR, "*.c"))

    # Run header parser
    print("Parsing header files...")
    for f in header_files:
        if not run_parser(HEADER_PARSER, f, HEADER_DB):
            print(f"Failed to parse header: {f}")

    # Run impl parser
    print("Parsing implementation files...")
    for f in impl_files:
        if not run_parser(IMPL_PARSER, f, IMPL_DB):
            print(f"Failed to parse implementation: {f}")

    # Check header DB
    print("\nChecking header database...")
    exit_code = 0
    if not os.path.exists(HEADER_DB):
        print("Header DB not created!")
        exit_code = 1
    else:
        missing = check_tables(HEADER_DB)
        if missing:
            print(f"Header DB missing tables: {missing}")
            exit_code = 1
        for f in header_files:
            if not check_file_entry(HEADER_DB, f):
                print(f"Header file not registered in DB: {f}")
                exit_code = 1
            else:
                # Check for at least one definition if file is non-empty
                if os.path.getsize(f) > 0:
                    conn = sqlite3.connect(HEADER_DB)
                    cur = conn.cursor()
                    cur.execute("SELECT id FROM files WHERE filepath = ?", (os.path.abspath(f),))
                    row = cur.fetchone()
                    if row and not check_definitions(HEADER_DB, row[0]):
                        print(f"No definitions found for header file: {f}")
                        exit_code = 1
                    conn.close()

    # Check impl DB
    print("\nChecking implementation database...")
    if not os.path.exists(IMPL_DB):
        print("Implementation DB not created!")
        exit_code = 1
    else:
        missing = check_tables(IMPL_DB)
        if missing:
            print(f"Implementation DB missing tables: {missing}")
            exit_code = 1
        for f in impl_files:
            if not check_file_entry(IMPL_DB, f):
                print(f"Implementation file not registered in DB: {f}")
                exit_code = 1
            else:
                if os.path.getsize(f) > 0:
                    conn = sqlite3.connect(IMPL_DB)
                    cur = conn.cursor()
                    cur.execute("SELECT id FROM files WHERE filepath = ?", (os.path.abspath(f),))
                    row = cur.fetchone()
                    if row and not check_definitions(IMPL_DB, row[0]):
                        print(f"No definitions found for implementation file: {f}")
                        exit_code = 1
                    conn.close()

    print("\nTest automation complete.")
    if exit_code == 0:
        print("All checks passed.")
    else:
        print("Some checks failed. See above for details.")
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
