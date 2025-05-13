import os
import glob
import subprocess
import sqlite3
import sys
import pytest
import shutil

# --- Constants ---
# Using pytest's tmp_path fixture for test-specific directories/files
HEADER_PARSER = "c_cxx_source_parser/header_parser.py" # Assuming this is relative to project root or in PATH
IMPL_PARSER = "c_cxx_source_parser/impl_parser.py"   # Assuming this is relative to project root or in PATH
TEST_DATA_DIR = "tests/test_targets" # Original location of test C/C++ files
REQUIRED_TABLES = [
    "files", "macros", "functions", "structs_unions", "enums", "typedefs", "variables"
]

# --- Helper Functions (Mostly unchanged from original) ---

def run_parser(script_path, file_path, db_path):
    """Runs a parser script on a file, directing output to a database."""
    # Ensure the script path is runnable, adjust if necessary based on project structure
    # If scripts are not directly executable, might need to prepend sys.executable
    cmd = [sys.executable, script_path, file_path, "-db", db_path]
    try:
        # Use check=True to raise CalledProcessError on failure
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8')
        # Optional: log stdout for debugging
        # print(f"Parser {os.path.basename(script_path)} output for {os.path.basename(file_path)}:\n{result.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error running {os.path.basename(script_path)} on {os.path.basename(file_path)}:\n{e.stderr}")
        return False
    except Exception as e:
        print(f"Exception running {os.path.basename(script_path)} on {os.path.basename(file_path)}: {e}")
        return False

def check_tables(db_path):
    """Checks if all required tables exist in the database."""
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = {row[0] for row in cur.fetchall()}
            missing = [t for t in REQUIRED_TABLES if t not in tables]
            return missing
    except Exception as e:
        print(f"Error checking tables in {db_path}: {e}")
        # Indicate failure by returning all tables as missing
        return REQUIRED_TABLES

def check_file_entry(db_path, file_path):
    """Checks if a specific file has an entry in the 'files' table."""
    abs_file_path = os.path.abspath(file_path)
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM files WHERE filepath = ?", (abs_file_path,))
            row = cur.fetchone()
            return row[0] if row else None # Return file_id if found, else None
    except Exception as e:
        print(f"Error checking file entry for {abs_file_path} in {db_path}: {e}")
        return None

def check_definitions(db_path, file_id):
    """Checks if any definitions exist for a given file_id in the definition tables."""
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            # Check tables other than 'files'
            for table in REQUIRED_TABLES[1:]:
                cur.execute(f"SELECT 1 FROM {table} WHERE file_id = ? LIMIT 1", (file_id,))
                if cur.fetchone():
                    return True # Found at least one definition
            return False # No definitions found in any relevant table
    except Exception as e:
        print(f"Error checking definitions for file_id {file_id} in {db_path}: {e}")
        return False


# --- Pytest Fixtures ---

@pytest.fixture(scope="module")
def test_environment(tmp_path_factory):
    """
    Sets up the test environment for the module.
    - Creates temporary directories for databases and target files.
    - Copies test target files.
    - Runs the parsers.
    - Yields paths to the databases and target files.
    """
    # Create temporary directories managed by pytest
    base_temp_dir = tmp_path_factory.mktemp("parser_tests")
    temp_target_dir = base_temp_dir / "test_targets"
    temp_db_dir = base_temp_dir / "db"
    temp_target_dir.mkdir()
    temp_db_dir.mkdir()

    header_db_path = str(temp_db_dir / "definitions_test.db")
    impl_db_path = str(temp_db_dir / "implementations_test.db")

    # --- Copy test files ---
    # Check if the original test data directory exists
    if not os.path.isdir(TEST_DATA_DIR):
        pytest.fail(f"Test data directory not found: {TEST_DATA_DIR}")

    try:
        # Copy contents of TEST_DATA_DIR to temp_target_dir
        for item in os.listdir(TEST_DATA_DIR):
            s = os.path.join(TEST_DATA_DIR, item)
            d = os.path.join(temp_target_dir, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, symlinks=False, ignore=None)
            else:
                shutil.copy2(s, d)
    except Exception as e:
         pytest.fail(f"Failed to copy test data from {TEST_DATA_DIR} to {temp_target_dir}: {e}")

    # Discover files in the temporary location
    header_files = glob.glob(os.path.join(temp_target_dir, "*.h"))
    impl_files = glob.glob(os.path.join(temp_target_dir, "*.c")) # Assuming .c files

    if not header_files and not impl_files:
         pytest.skip(f"No .h or .c files found in the test data directory: {temp_target_dir}")


    # --- Run Parsers ---
    print("\nRunning parsers...")
    header_parse_success = True
    for f in header_files:
        if not run_parser(HEADER_PARSER, f, header_db_path):
            header_parse_success = False # Log failure but continue

    impl_parse_success = True
    for f in impl_files:
        if not run_parser(IMPL_PARSER, f, impl_db_path):
            impl_parse_success = False # Log failure but continue

    # Yield data needed by tests
    yield {
        "header_db": header_db_path,
        "impl_db": impl_db_path,
        "header_files": header_files,
        "impl_files": impl_files,
        "header_parse_success": header_parse_success,
        "impl_parse_success": impl_parse_success,
        "temp_dir": str(base_temp_dir) # For potential cleanup or inspection
    }

    # --- Teardown (handled by pytest's tmp_path_factory) ---
    print(f"\nTest environment teardown (directory {base_temp_dir} will be removed).")


# --- Test Functions ---

@pytest.mark.usefixtures("test_environment")
class TestParsers:

    # Basic checks for parser execution and DB creation
    def test_header_parser_execution(self, test_environment):
        """Verify that the header parser script seemed to execute."""
        if not test_environment["header_files"]:
             pytest.skip("No header files found to parse.")
        # We check success flag set during fixture setup
        assert test_environment["header_parse_success"], "Header parser script reported errors during execution."
        assert os.path.exists(test_environment["header_db"]), "Header database file was not created."

    def test_impl_parser_execution(self, test_environment):
        """Verify that the implementation parser script seemed to execute."""
        if not test_environment["impl_files"]:
             pytest.skip("No implementation files found to parse.")
        # We check success flag set during fixture setup
        assert test_environment["impl_parse_success"], "Implementation parser script reported errors during execution."
        assert os.path.exists(test_environment["impl_db"]), "Implementation database file was not created."

    # Database structure checks
    def test_header_db_tables(self, test_environment):
        """Check if all required tables exist in the header database."""
        if not os.path.exists(test_environment["header_db"]):
             pytest.skip("Header DB does not exist, skipping table check.")
        missing_tables = check_tables(test_environment["header_db"])
        assert not missing_tables, f"Header DB is missing tables: {missing_tables}"

    def test_impl_db_tables(self, test_environment):
        """Check if all required tables exist in the implementation database."""
        if not os.path.exists(test_environment["impl_db"]):
             pytest.skip("Implementation DB does not exist, skipping table check.")
        missing_tables = check_tables(test_environment["impl_db"])
        assert not missing_tables, f"Implementation DB is missing tables: {missing_tables}"

    # File registration and definition checks (Parameterized)
    @pytest.mark.parametrize("file_type", ["header", "impl"])
    def test_file_registration_and_definitions(self, test_environment, file_type):
        """
        Check each file:
        1. Is registered in the correct database's 'files' table.
        2. Has associated definitions if the file is not empty.
        """
        if file_type == "header":
            db_path = test_environment["header_db"]
            files = test_environment["header_files"]
            parser_success = test_environment["header_parse_success"]
        else: # impl
            db_path = test_environment["impl_db"]
            files = test_environment["impl_files"]
            parser_success = test_environment["impl_parse_success"]

        if not files:
             pytest.skip(f"No {file_type} files found.")
        if not parser_success:
             pytest.skip(f"{file_type.capitalize()} parser failed, skipping DB content checks.")
        if not os.path.exists(db_path):
             pytest.skip(f"{file_type.capitalize()} DB does not exist, skipping content checks.")

        all_files_registered = True
        all_non_empty_files_have_defs = True
        unregistered_files = []
        files_without_defs = []

        for f in files:
            file_id = check_file_entry(db_path, f)
            if file_id is None:
                all_files_registered = False
                unregistered_files.append(os.path.basename(f))
                continue # Skip definition check if file not registered

            # Check for definitions only if file is not empty and was registered
            if os.path.getsize(f) > 0:
                if not check_definitions(db_path, file_id):
                    all_non_empty_files_have_defs = False
                    files_without_defs.append(os.path.basename(f))

        assert all_files_registered, f"Missing registration in {file_type} DB for files: {unregistered_files}"
        assert all_non_empty_files_have_defs, f"No definitions found in {file_type} DB for non-empty files: {files_without_defs}"

# Run tests when file is executed directly
if __name__ == "__main__":
    print("Running tests using pytest.main()...")
    # Exit with pytest's return code to indicate success/failure
    sys.exit(pytest.main([__file__]))