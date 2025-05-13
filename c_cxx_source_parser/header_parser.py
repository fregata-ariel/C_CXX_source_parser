import sys
import os
import sqlite3
import argparse
import time
from clang.cindex import Index, Config, CursorKind, TypeKind, TranslationUnit, StorageClass
import clang.cindex

# --- グローバル変数 ---
# libclangのライブラリファイルのパス (環境に合わせて変更が必要な場合あり)
# 例: Ubuntuの場合 '/usr/lib/llvm-14/lib/libclang.so.1' など
#     macOS (brew) の場合 '/opt/homebrew/opt/llvm/lib/libclang.dylib' など
# 自動で見つからない場合は設定してください
LIBCLANG_PATH = None 
# LIBCLANG_PATH = '/path/to/libclang.so' # Linux の例
# LIBCLANG_PATH = '/path/to/libclang.dylib' # macOS の例

# --- データベース関連 ---

def setup_database(db_path):
    """SQLiteデータベースをセットアップし、テーブルを作成する"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # ファイル管理テーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE NOT NULL,
            last_parsed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # マクロ定義テーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS macros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            body TEXT,
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
        )
    ''')
    # インデックスを作成 (検索高速化のため)
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_macro_name ON macros (name)')


    # 関数定義テーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS functions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            return_type TEXT,
            parameters TEXT, -- パラメータは単純なテキストとして保存 (例: "int a, const char* b")
            is_declaration INTEGER DEFAULT 1, -- 1: 宣言, 0: 定義 (ヘッダでは主に宣言)
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_func_name ON functions (name)')


    # 構造体/共用体定義テーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS structs_unions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            kind TEXT NOT NULL, -- 'struct' or 'union'
            name TEXT,         -- 匿名の場合は NULL
            members TEXT,      -- メンバは単純なテキストとして保存 (例: "int x; float y;")
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_struct_name ON structs_unions (name)')


    # 列挙型定義テーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS enums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            name TEXT,         -- 匿名の場合は NULL
            constants TEXT,    -- 定数は単純なテキストとして保存 (例: "RED=1, GREEN, BLUE=5")
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_enum_name ON enums (name)')


    # Typedef定義テーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS typedefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            underlying_type TEXT,
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_typedef_name ON typedefs (name)')


    # グローバル変数定義テーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS variables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            type TEXT,
            is_extern INTEGER DEFAULT 0,
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_var_name ON variables (name)')


    conn.commit()
    return conn

def clear_definitions_for_file(conn, file_id):
    """特定のファイルIDに関連する定義をDBから削除する"""
    cursor = conn.cursor()
    tables = ['macros', 'functions', 'structs_unions', 'enums', 'typedefs', 'variables']
    for table in tables:
        cursor.execute(f"DELETE FROM {table} WHERE file_id = ?", (file_id,))
    conn.commit()

def add_file_record(conn, filepath):
    """ファイルをDBに記録し、既存の場合は更新、新規の場合は挿入してIDを返す"""
    cursor = conn.cursor()
    filepath_abs = os.path.abspath(filepath)
    now = time.strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute("SELECT id FROM files WHERE filepath = ?", (filepath_abs,))
    result = cursor.fetchone()

    if result:
        file_id = result[0]
        cursor.execute("UPDATE files SET last_parsed_at = ? WHERE id = ?", (now, file_id))
        print(f"Updating records for file: {filepath_abs} (ID: {file_id})")
        # 既存の定義をクリア
        clear_definitions_for_file(conn, file_id)
    else:
        cursor.execute("INSERT INTO files (filepath, last_parsed_at) VALUES (?, ?)", (filepath_abs, now))
        file_id = cursor.lastrowid
        print(f"Adding new record for file: {filepath_abs} (ID: {file_id})")

    conn.commit()
    return file_id

# --- Clang AST 解析 ---

def get_macro_body(cursor:clang.cindex.Cursor):
    """マクロの本体を取得する試み"""
    tokens = list(cursor.get_tokens())
    if len(tokens) > 1:
        # 最初のトークン（マクロ名）を除き、残りを結合
        # トークン間のスペースを保持するように試みる
        body = ""
        last_token_end = tokens[0].extent.end.column
        for i in range(1, len(tokens)):
            token = tokens[i]
            space = " " * max(0, token.extent.start.column - last_token_end)
            body += space + token.spelling
            last_token_end = token.extent.end.column
        return body.strip()
    return None # 本体がないか、取得失敗

def get_function_params(cursor:clang.cindex.Cursor):
    """関数のパラメータリストを文字列として取得する"""
    params = []
    for arg in cursor.get_arguments():
        param_type = arg.type.spelling
        param_name = arg.spelling
        params.append(f"{param_type} {param_name}".strip())
    return ", ".join(params)

def get_struct_union_members(cursor:clang.cindex.Cursor):
    """構造体/共用体のメンバを文字列として取得する"""
    members = []
    for child in cursor.get_children():
        if child.kind == CursorKind.FIELD_DECL:
            members.append(f"{child.type.spelling} {child.spelling};")
        # ネストされた構造体/共用体/enumなどの扱いはここでは省略
    return " ".join(members)

def get_enum_constants(cursor:clang.cindex.Cursor):
    """列挙型の定数を文字列として取得する"""
    constants = []
    for child in cursor.get_children():
        if child.kind == CursorKind.ENUM_CONSTANT_DECL:
            const_name = child.spelling
            const_val = child.enum_value
            constants.append(f"{const_name}={const_val}") # 値も取得
            #constants.append(const_name) # 名前だけの場合
    return ", ".join(constants)


def traverse_ast(
        cursor:clang.cindex.Cursor,
        db_conn:sqlite3.Connection,
        file_id:int,
        target_filepath:str
        ) -> None:
    """Recursively traverse the AST and add definitions to the database"""
    db_cursor = db_conn.cursor()

    # Check if the cursor is in the target file (exclude included headers)
    # Location may be None for CursorKind like UNEXPOSED_DECL
    if cursor.location and cursor.location.file and \
       os.path.abspath(cursor.location.file.name) != target_filepath:
        return # This cursor is not in target file
    # Debugging: Display current cursor type and name
    # print(f"Visiting: {cursor.kind} - {cursor.spelling} at {cursor.location}")

    # --- Process various definitions ---

    if cursor.kind == CursorKind.MACRO_DEFINITION:
        # libclang may not properly get function-like macro bodies
        # Either get just the macro name, or try with get_tokens
        name = cursor.spelling or None
        body = get_macro_body(cursor) or ""
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"

        # Check if this macro already exists for the file_id and name
        db_cursor.execute("SELECT id FROM macros WHERE file_id = ? AND name = ?", (file_id, name))
        existing_macro = db_cursor.fetchone()
        if existing_macro:
            # Update the existing record
            db_cursor.execute("UPDATE macros SET body=?, location=? WHERE id=?", (body, location, existing_macro[0]))
        else:
            if name: # Ignore macros without names (e.g., just #define)
                db_cursor.execute("INSERT INTO macros (file_id, name, body, location) VALUES (?, ?, ?, ?)",
                                (file_id, name, body, location))

    elif cursor.kind == CursorKind.FUNCTION_DECL:
        name = cursor.spelling or None  # May be anonymous struct
        return_type = cursor.result_type.spelling or ""
        params = get_function_params(cursor) or ""
        is_declaration = 1 if not cursor.is_definition() else 0
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"

        # Check for existing function
        if cursor.is_definition(): # Record only struct definitions (with content)
            db_cursor.execute("SELECT id FROM functions WHERE file_id = ? AND name = ?", (file_id, name))
            existing_func = db_cursor.fetchone()
            if existing_func:
                # Update the function record
                db_cursor.execute("UPDATE functions SET return_type=?, parameters=?, is_declaration=?, location=? WHERE id=?",
                                (return_type, params, is_declaration, location, existing_func[0]))
            else:
                # Insert new function
                db_cursor.execute("INSERT INTO functions (file_id, name, return_type, parameters, is_declaration, location) VALUES (?, ?, ?, ?, ?, ?)",
                                (file_id, name, return_type, params, is_declaration, location))

    elif cursor.kind == CursorKind.STRUCT_DECL:
        name = cursor.spelling or None # May be anonymous struct
        kind = 'struct'
        members = get_struct_union_members(cursor) or ""
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"

        # Ensure it's a definition (not just declaration)
        if cursor.is_definition():
            # Check for existing struct
            db_cursor.execute("SELECT id FROM structs_unions WHERE file_id = ? AND name = ? AND kind = ?",
                              (file_id, name, kind))
            existing_struct = db_cursor.fetchone()
            if existing_struct:
                # Update the struct record
                db_cursor.execute("UPDATE structs_unions SET members=?, location=? WHERE id=?",
                                  (members, location, existing_struct[0]))
            else:
                # Insert new struct
                db_cursor.execute("INSERT INTO structs_unions (file_id, kind, name, members, location) VALUES (?, ?, ?, ?, ?)",
                                  (file_id, kind, name, members, location))

    elif cursor.kind == CursorKind.UNION_DECL:
        name = cursor.spelling or None # May be anonymous union
        kind = 'union'
        members = get_struct_union_members(cursor) or ""
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"

        # Ensure it's a definition (not just declaration)
        if cursor.is_definition():
            # Check for existing union
            db_cursor.execute("SELECT id FROM structs_unions WHERE file_id = ? AND name = ? AND kind = ?",
                              (file_id, name, kind))
            existing_union = db_cursor.fetchone()
            if existing_union:
                # Update the union record
                db_cursor.execute("UPDATE structs_unions SET members=?, location=? WHERE id=?",
                                  (members, location, existing_union[0]))
            else:
                # Insert new union
                db_cursor.execute("INSERT INTO structs_unions (file_id, kind, name, members, location) VALUES (?, ?, ?, ?, ?)",
                                  (file_id, name, kind, members, location))

    elif cursor.kind == CursorKind.ENUM_DECL:
        name = cursor.spelling or None # May be anonymous enum
        constants = get_enum_constants(cursor) or ""
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"

        # Ensure it's a definition (not just declaration)
        if cursor.is_definition():
            # Check for existing enum
            db_cursor.execute("SELECT id FROM enums WHERE file_id = ? AND name = ?", (file_id, name))
            existing_enum = db_cursor.fetchone()
            if existing_enum:
                # Update the enum record
                db_cursor.execute("UPDATE enums SET constants=?, location=? WHERE id=?",
                                  (constants, location, existing_enum[0]))
            else:
                # Insert new enum
                db_cursor.execute("INSERT INTO enums (file_id, name, constants, location) VALUES (?, ?, ?, ?)",
                                  (file_id, name, constants, location))

    elif cursor.kind == CursorKind.TYPEDEF_DECL:
        name = cursor.spelling or None
        underlying_type = cursor.underlying_typedef_type.spelling or ""
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"

        # Check for existing typedef
        db_cursor.execute("SELECT id FROM typedefs WHERE file_id = ? AND name = ?", (file_id, name))
        existing_TYPEDEF = db_cursor.fetchone()
        if existing_TYPEDEF:
            # Update the typedef record
            db_cursor.execute("UPDATE typedefs SET underlying_type=?, location=? WHERE id=?",
                              (underlying_type, location, existing_TYPEDEF[0]))
        else:
            # Insert new typedef
            db_cursor.execute("INSERT INTO typedefs (file_id, name, underlying_type, location) VALUES (?, ?, ?, ?)",
                              (file_id, name, underlying_type, location))

    elif cursor.kind == CursorKind.VAR_DECL:
        # Only handle file-scope variables (global variables and static variables)
        # Local variables in functions have cursor.semantic_parent.kind as FUNCTION_DECL
        if cursor.semantic_parent.kind == CursorKind.TRANSLATION_UNIT:
            name = cursor.spelling or None
            var_type = cursor.type.spelling or ""
            is_extern = 1 if cursor.storage_class == StorageClass.EXTERN else 0
            location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"

            # Check for existing variable
            db_cursor.execute("SELECT id FROM variables WHERE file_id = ? AND name = ?", (file_id, name))
            existing_var = db_cursor.fetchone()
            if existing_var:
                # Update the variable record
                db_cursor.execute("UPDATE variables SET type=?, is_extern=?, location=? WHERE id=?",
                                  (var_type, is_extern, location, existing_var[0]))
            else:
                # Insert new variable
                db_cursor.execute("INSERT INTO variables (file_id, name, type, is_extern, location) VALUES (?, ?, ?, ?, ?)",
                                  (file_id, name, var_type, is_extern, location))

    # --- Recursively explore child nodes ---
    for child in cursor.get_children():
        traverse_ast(child, db_conn, file_id, target_filepath)


# --- メイン処理 ---
def main():
    parser = argparse.ArgumentParser(description='Parse C/C++ header file and store definitions in SQLite.')
    parser.add_argument('header_file', help='Path to the C/C++ header file to parse.')
    parser.add_argument('-db', '--database', default='definitions.db', help='Path to the SQLite database file (default: definitions.db).')
    parser.add_argument('-I', '--include', action='append', default=[], help='Add directory to include search path.')
    parser.add_argument('-D', '--define', action='append', default=[], help='Define a macro (e.g., -DDEBUG=1).')
    parser.add_argument('--libclang', help=f'Path to libclang library file (e.g., {LIBCLANG_PATH or "/path/to/libclang.so"})')
    parser.add_argument('--lang', choices=['c', 'c++'], default=None, help='Force language standard (e.g., c++11). Tries to guess from extension if not provided.')
    parser.add_argument('--std', default=None, help='Set C/C++ standard (e.g., c11, c++14).')


    args = parser.parse_args()

    header_filepath = args.header_file
    db_filepath = args.database
    clang_args = []

    # インクルードパスを追加
    for include_dir in args.include:
        clang_args.append(f'-I{include_dir}')

    # マクロ定義を追加
    for define_macro in args.define:
        clang_args.append(f'-D{define_macro}')

    # 言語と標準を設定
    language = args.lang
    if not language:
        if header_filepath.endswith(('.hpp', '.hxx', '.hh', '.cpp', '.cxx', '.cc')):
            language = 'c++'
        else:
            language = 'c' # デフォルトはC

    if language == 'c++':
        clang_args.append('-x')
        clang_args.append('c++')
        std_arg = args.std or 'c++11' # デフォルト C++11
        clang_args.append(f'-std={std_arg}')
    else:
        clang_args.append('-x')
        clang_args.append('c')
        if args.std:
            clang_args.append(f'-std={args.std}')


    print(f"Parsing: {header_filepath}")
    print(f"Database: {db_filepath}")
    print(f"Clang Args: {' '.join(clang_args)}")

    # libclangのパス設定
    libclang_path_to_use = args.libclang or LIBCLANG_PATH
    if libclang_path_to_use:
        if os.path.exists(libclang_path_to_use):
            Config.set_library_file(libclang_path_to_use)
            print(f"Using libclang: {libclang_path_to_use}")
        else:
            print(f"Warning: Specified libclang path not found: {libclang_path_to_use}", file=sys.stderr)
            print("Attempting to find libclang automatically...", file=sys.stderr)
            # Config.set_library_file() を呼ばなければ自動探索を試みる
    else:
         print("Attempting to find libclang automatically...")


    try:
        # Clangインデックスを作成
        index = Index.create()

        # ヘッダファイルをパース
        # TU_SKIP_FUNCTION_BODIES: 関数の本体をスキップ（ヘッダ解析では不要なことが多い）
        # TU_DETAILED_PREPROCESSING_RECORD: マクロ定義などをより詳細に取得
        parse_options = (
            TranslationUnit.PARSE_SKIP_FUNCTION_BODIES
        )
        tu = index.parse(
            header_filepath,
            args=clang_args,
            options=parse_options
        )

        # パースエラーチェック
        has_errors = False
        for diag in tu.diagnostics:
            # エラーレベルが Error または Fatal の場合
            if diag.severity >= diag.Error:
                print(f"Parse Error: {diag.spelling} at {diag.location}", file=sys.stderr)
                has_errors = True
        
        # エラーがある場合でも処理を続行するかどうか。ここでは警告して続行する。
        # if has_errors:
        #     print("Errors occurred during parsing. Exiting.", file=sys.stderr)
        #     sys.exit(1)

        # データベース接続とセットアップ
        conn = setup_database(db_filepath)

        # ファイルレコードを追加/更新し、ファイルIDを取得
        target_filepath_abs = os.path.abspath(header_filepath)
        file_id = add_file_record(conn, header_filepath)

        # ASTを走査して定義をDBに追加
        print("Traversing AST and storing definitions...")
        traverse_ast(tu.cursor, conn, file_id, target_filepath_abs)

        # データベースへの変更をコミット
        conn.commit()
        print("Committing changes to database.")

        # 接続を閉じる
        conn.close()
        print("Done.")

    except ImportError:
        print("Error: libclang Python bindings not found.", file=sys.stderr)
        print("Please install with: pip install clang", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
         print(f"Error: Header file not found: {header_filepath}", file=sys.stderr)
         sys.exit(1)
    except sqlite3.Error as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        # libclangが見つからない場合もここに来ることがある
        if 'library file' in str(e):
             print("Error: Failed to find libclang library.", file=sys.stderr)
             print("Please ensure LLVM/Clang is installed and accessible.", file=sys.stderr)
             print("You might need to set the LIBCLANG_PATH variable in the script or use the --libclang argument.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
