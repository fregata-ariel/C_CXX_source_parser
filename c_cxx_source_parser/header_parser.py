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
    # グローバルコンテキスト用のダミーファイルレコードを挿入
    global_context_filepath = '(global_context)'
    cursor.execute("INSERT OR IGNORE INTO files (filepath) VALUES (?)", (global_context_filepath,))
    cursor.execute("SELECT id FROM files WHERE filepath = ?", (global_context_filepath,))
    global_file_id_record = cursor.fetchone()
    if not global_file_id_record:
        raise Exception("Failed to insert or find global_context file record.")
    global_file_id_for_global_ns = global_file_id_record[0]

    # 名前空間テーブル (新規追加)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS namespaces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            parent_namespace_id INTEGER,
            file_id INTEGER NOT NULL,
            location TEXT NOT NULL,
            full_qualified_name TEXT NOT NULL UNIQUE,
            FOREIGN KEY (parent_namespace_id) REFERENCES namespaces (id) ON DELETE CASCADE,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_namespaces_name ON namespaces (name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_namespaces_parent_id ON namespaces (parent_namespace_id)')
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_namespaces_fqn ON namespaces (full_qualified_name)')
    
    # グローバル名前空間レコードを挿入
    global_namespace_name = "(global)"
    global_namespace_fqn = "(global)"
    cursor.execute("""
        INSERT OR IGNORE INTO namespaces (name, parent_namespace_id, file_id, location, full_qualified_name)
        VALUES (?, NULL, ?, ?, ?)
    """, (global_namespace_name, global_file_id_for_global_ns, "N/A", global_namespace_fqn))


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
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_macro_name ON macros (name)')


    # 関数定義テーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS functions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            namespace_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            return_type TEXT,
            parameters TEXT,
            is_declaration INTEGER DEFAULT 1,
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE,
            FOREIGN KEY (namespace_id) REFERENCES namespaces (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_func_name ON functions (name)')


    # 構造体/共用体定義テーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS structs_unions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            namespace_id INTEGER NOT NULL,
            kind TEXT NOT NULL, 
            name TEXT,
            members TEXT,
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE,
            FOREIGN KEY (namespace_id) REFERENCES namespaces (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_struct_name ON structs_unions (name)')


    # 列挙型定義テーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS enums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            namespace_id INTEGER NOT NULL,
            name TEXT,
            constants TEXT,
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE,
            FOREIGN KEY (namespace_id) REFERENCES namespaces (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_enum_name ON enums (name)')


    # Typedef定義テーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS typedefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            namespace_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            underlying_type TEXT,
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE,
            FOREIGN KEY (namespace_id) REFERENCES namespaces (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_typedef_name ON typedefs (name)')


    # グローバル変数定義テーブル
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS variables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            namespace_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            type TEXT,
            is_extern INTEGER DEFAULT 0,
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE,
            FOREIGN KEY (namespace_id) REFERENCES namespaces (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_var_name ON variables (name)')


    conn.commit()
    return conn

def clear_definitions_for_file(conn, file_id):
    """特定のファイルIDに関連する定義をDBから削除する"""
    cursor = conn.cursor()
    # namespaces テーブルはファイル横断でFQNで管理するため、ファイル削除時に単純に消さない。
    # 消す場合は、このファイルでしか定義されていない名前空間のみを消すなど複雑なロジックが必要。
    # 今回は簡単のため、定義テーブルのみをクリアする。
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
        clear_definitions_for_file(conn, file_id)
    else:
        cursor.execute("INSERT INTO files (filepath, last_parsed_at) VALUES (?, ?)", (filepath_abs, now))
        file_id = cursor.lastrowid
        print(f"Adding new record for file: {filepath_abs} (ID: {file_id})")

    conn.commit()
    return file_id

# --- 新しいヘルパー関数 ---
def _get_global_namespace_id(db_cursor):
    """グローバル名前空間のIDを取得する"""
    db_cursor.execute("SELECT id FROM namespaces WHERE full_qualified_name = ?", ("(global)",))
    result = db_cursor.fetchone()
    if not result:
        raise Exception("Global namespace not found in the database. setup_database might have failed.")
    return result[0]

def _generate_fqn(parent_fqn, current_name_str, is_anonymous, file_path, location):
    """完全修飾名を生成する"""
    if is_anonymous:
        # 匿名名前空間のFQNを一意にする
        unique_suffix = f"{os.path.basename(file_path)}_{location.line}_{location.column}"
        name_part = f"(anonymous)_{unique_suffix}"
    else:
        name_part = current_name_str
    
    if parent_fqn == "(global)":
        return f"(global)::{name_part}"
    return f"{parent_fqn}::{name_part}"

def _get_or_create_namespace_db_entry(db_conn, db_cursor, fqn, name_for_db, parent_db_id, file_id, location_str):
    """FQNに基づいてDBからnamespaceエントリを取得または作成する"""
    db_cursor.execute("SELECT id FROM namespaces WHERE full_qualified_name = ?", (fqn,))
    row = db_cursor.fetchone()
    if row:
        return row[0]
    else:
        db_cursor.execute(
            "INSERT INTO namespaces (name, parent_namespace_id, file_id, location, full_qualified_name) VALUES (?, ?, ?, ?, ?)",
            (name_for_db, parent_db_id, file_id, location_str, fqn)
        )
        return db_cursor.lastrowid

# --- Clang AST 解析 (既存ヘルパー) ---

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
            constants.append(f"{const_name}={const_val}")
    return ", ".join(constants)

# --- traverse_ast (大幅に修正) ---

def traverse_ast(
        cursor: clang.cindex.Cursor,
        db_conn: sqlite3.Connection,
        db_cursor: sqlite3.Cursor,
        file_id: int,
        target_filepath: str,
        scope_stack: list
) -> None:
    """Recursively traverse the AST and add definitions to the database"""

    # Check if the cursor is in the target file (exclude included headers)
    if not (cursor.location and cursor.location.file and os.path.abspath(cursor.location.file.name) == target_filepath):
        # ただし、NAMESPACEの場合はヘッダファイル由来でも処理を続けたい場合があるかもしれないが、
        # 今回は指定されたファイル内の定義のみを対象とする
        # 親が対象ファイル内なら子も処理する、というロジックが必要な場合がある
        return

    current_ns_id, parent_fqn = scope_stack[-1]

    # --- Process various definitions ---

    if cursor.kind == CursorKind.NAMESPACE:
        name_str = cursor.spelling
        is_anonymous = (name_str == "")
        location_str = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"
        
        fqn = _generate_fqn(parent_fqn, name_str, is_anonymous, target_filepath, cursor.location)
        db_name = "(anonymous)" if is_anonymous else name_str

        new_ns_id = _get_or_create_namespace_db_entry(db_conn, db_cursor, fqn, db_name, current_ns_id, file_id, location_str)

        scope_stack.append((new_ns_id, fqn))
        for child in cursor.get_children():
            traverse_ast(child, db_conn, db_cursor, file_id, target_filepath, scope_stack)
        scope_stack.pop()
        return # 名前空間ノード自体の処理はここまで

    elif cursor.kind == CursorKind.MACRO_DEFINITION:
        # マクロは名前空間に属さないので、従来通りの処理
        name = cursor.spelling or None
        body = get_macro_body(cursor) or ""
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"
        if name:
            db_cursor.execute("INSERT INTO macros (file_id, name, body, location) VALUES (?, ?, ?, ?)",
                              (file_id, name, body, location))

    elif cursor.kind == CursorKind.FUNCTION_DECL:
        name = cursor.spelling
        return_type = cursor.result_type.spelling
        params = get_function_params(cursor)
        is_declaration = 1 if not cursor.is_definition() else 0
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"
        db_cursor.execute(
            "INSERT INTO functions (file_id, namespace_id, name, return_type, parameters, is_declaration, location) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (file_id, current_ns_id, name, return_type, params, is_declaration, location)
        )

    elif cursor.kind == CursorKind.STRUCT_DECL and cursor.is_definition():
        name = cursor.spelling or None
        kind = 'struct'
        members = get_struct_union_members(cursor)
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"
        db_cursor.execute(
            "INSERT INTO structs_unions (file_id, namespace_id, kind, name, members, location) VALUES (?, ?, ?, ?, ?, ?)",
            (file_id, current_ns_id, kind, name, members, location)
        )

    elif cursor.kind == CursorKind.UNION_DECL and cursor.is_definition():
        name = cursor.spelling or None
        kind = 'union'
        members = get_struct_union_members(cursor)
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"
        db_cursor.execute(
            "INSERT INTO structs_unions (file_id, namespace_id, kind, name, members, location) VALUES (?, ?, ?, ?, ?, ?)",
            (file_id, current_ns_id, kind, name, members, location)
        )

    elif cursor.kind == CursorKind.ENUM_DECL and cursor.is_definition():
        name = cursor.spelling or None
        constants = get_enum_constants(cursor)
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"
        db_cursor.execute(
            "INSERT INTO enums (file_id, namespace_id, name, constants, location) VALUES (?, ?, ?, ?, ?)",
            (file_id, current_ns_id, name, constants, location)
        )

    elif cursor.kind == CursorKind.TYPEDEF_DECL:
        name = cursor.spelling
        underlying_type = cursor.underlying_typedef_type.spelling
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"
        db_cursor.execute(
            "INSERT INTO typedefs (file_id, namespace_id, name, underlying_type, location) VALUES (?, ?, ?, ?, ?)",
            (file_id, current_ns_id, name, underlying_type, location)
        )

    elif cursor.kind == CursorKind.VAR_DECL:
        # ファイルスコープの変数のみ対象
        if cursor.semantic_parent.kind == CursorKind.TRANSLATION_UNIT or cursor.semantic_parent.kind == CursorKind.NAMESPACE:
            name = cursor.spelling
            var_type = cursor.type.spelling
            is_extern = 1 if cursor.storage_class == StorageClass.EXTERN else 0
            location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"
            db_cursor.execute(
                "INSERT INTO variables (file_id, namespace_id, name, type, is_extern, location) VALUES (?, ?, ?, ?, ?, ?)",
                (file_id, current_ns_id, name, var_type, is_extern, location)
            )

    # --- Recursively explore child nodes (but not for namespaces, as handled above) ---
    # 名前空間以外のノードの子も再帰的に探索する
    if cursor.kind != CursorKind.NAMESPACE:
        for child in cursor.get_children():
            traverse_ast(child, db_conn, db_cursor, file_id, target_filepath, scope_stack)


# --- メイン処理 (修正) ---
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

    for include_dir in args.include:
        clang_args.append(f'-I{include_dir}')
    for define_macro in args.define:
        clang_args.append(f'-D{define_macro}')

    language = args.lang
    if not language:
        if header_filepath.endswith(('.hpp', '.hxx', '.hh')):
            language = 'c++'
        else:
            language = 'c'

    if language == 'c++':
        clang_args.extend(['-x', 'c++'])
        std_arg = args.std or 'c++11'
        clang_args.append(f'-std={std_arg}')
    else: # language == 'c'
        clang_args.extend(['-x', 'c'])
        if args.std:
            clang_args.append(f'-std={args.std}')


    print(f"Parsing: {header_filepath}")
    print(f"Database: {db_filepath}")
    print(f"Clang Args: {' '.join(clang_args)}")

    if args.libclang and os.path.exists(args.libclang):
        Config.set_library_file(args.libclang)
        print(f"Using libclang: {args.libclang}")
    elif LIBCLANG_PATH and os.path.exists(LIBCLANG_PATH):
        Config.set_library_file(LIBCLANG_PATH)
        print(f"Using libclang from LIBCLANG_PATH: {LIBCLANG_PATH}")
    else:
        print("Attempting to find libclang automatically...")


    try:
        index = Index.create()
        parse_options = TranslationUnit.PARSE_SKIP_FUNCTION_BODIES | TranslationUnit.PARSE_DETAILED_PREPROCESSING_RECORD
        
        tu = index.parse(header_filepath, args=clang_args, options=parse_options)

        has_errors = False
        for diag in tu.diagnostics:
            if diag.severity >= diag.Error:
                print(f"Parse Error: {diag.spelling} at {diag.location}", file=sys.stderr)
                has_errors = True
        
        if has_errors:
            print("Warning: Errors occurred during parsing. Results might be incomplete.", file=sys.stderr)

        conn = setup_database(db_filepath)
        db_cursor = conn.cursor()

        target_filepath_abs = os.path.abspath(header_filepath)
        file_id = add_file_record(conn, header_filepath)

        # スコープスタックの初期化
        global_ns_id = _get_global_namespace_id(db_cursor)
        initial_scope_stack = [(global_ns_id, "(global)")]

        print("Traversing AST and storing definitions...")
        traverse_ast(tu.cursor, conn, db_cursor, file_id, target_filepath_abs, initial_scope_stack)

        conn.commit()
        print("Committing changes to database.")
        conn.close()
        print("Done.")

    except ImportError:
        print("Error: libclang Python bindings not found. Please install with: pip install clang", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Error: Header file not found: {header_filepath}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        if 'library file' in str(e):
            print("Error: Failed to find or load libclang library. Please ensure LLVM/Clang is installed and accessible.", file=sys.stderr)
            print("You might need to set the LIBCLANG_PATH variable in the script or use the --libclang argument.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()