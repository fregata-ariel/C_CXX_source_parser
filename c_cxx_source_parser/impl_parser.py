import sys
import os
import sqlite3
import argparse
import time
from clang.cindex import Index, Config, CursorKind, TypeKind, TranslationUnit, StorageClass

# --- グローバル変数 ---
# libclangのライブラリファイルのパス (環境に合わせて変更が必要な場合あり)
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

    # マクロ定義テーブル (変更なし)
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

    # 関数定義/宣言テーブル (namespace_id 追加, 拡張カラムは維持)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS functions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            namespace_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            return_type TEXT,
            parameters TEXT,
            is_declaration INTEGER NOT NULL,
            is_static INTEGER DEFAULT 0,
            parent_kind TEXT,
            parent_name TEXT,
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE,
            FOREIGN KEY (namespace_id) REFERENCES namespaces (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_func_name ON functions (name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_func_parent ON functions (parent_name)')

    # 構造体/共用体定義テーブル (namespace_id 追加)
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

    # 列挙型定義テーブル (namespace_id 追加)
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

    # Typedef定義テーブル (namespace_id 追加)
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

    # グローバル/静的変数 定義/宣言テーブル (namespace_id 追加, 拡張カラムは維持)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS variables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            namespace_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            type TEXT,
            is_extern INTEGER DEFAULT 0,
            is_static INTEGER DEFAULT 0,
            has_initializer INTEGER DEFAULT 0,
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
def get_macro_body(cursor):
    tokens = list(cursor.get_tokens())
    if len(tokens) > 1:
        body = ""
        last_token_end = tokens[0].extent.end.column
        for i in range(1, len(tokens)):
            token = tokens[i]
            space = " " * max(0, token.extent.start.column - last_token_end)
            body += space + token.spelling
            last_token_end = token.extent.end.column
        return body.strip()
    return None

def get_function_params(cursor):
    params = []
    try:
        for arg in cursor.get_arguments():
            param_name = arg.spelling or ""
            param_type = arg.type.spelling
            params.append(f"{param_type} {param_name}".strip())
    except Exception:
        try:
             func_type = cursor.type
             if func_type.kind in (TypeKind.FUNCTIONPROTO, TypeKind.FUNCTIONNOPROTO):
                 for i, arg_type in enumerate(func_type.argument_types()):
                     params.append(f"{arg_type.spelling} arg{i+1}")
        except Exception:
             return "..."
    return ", ".join(params)

def get_struct_union_members(cursor):
    members = []
    for child in cursor.get_children():
        if child.kind == CursorKind.FIELD_DECL:
            members.append(f"{child.type.spelling} {child.spelling};")
    return " ".join(members)

def get_enum_constants(cursor):
    constants = []
    for child in cursor.get_children():
        if child.kind == CursorKind.ENUM_CONSTANT_DECL:
            const_name = child.spelling
            const_val = child.enum_value
            constants.append(f"{const_name}={const_val}")
    return ", ".join(constants)

def has_initializer(cursor):
    """変数が初期化子を持つか簡易的にチェック"""
    for child in cursor.get_children():
        if child.kind.is_expression() or child.kind == CursorKind.INIT_LIST_EXPR:
            return 1
    return 0

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
    in_target_file = False
    if cursor.location and cursor.location.file:
        try:
            in_target_file = os.path.abspath(cursor.location.file.name) == target_filepath
        except FileNotFoundError:
            pass
    
    # We only care about definitions within the target file.
    if not in_target_file:
        return

    current_ns_id, parent_fqn = scope_stack[-1]

    # --- Process various definitions ---

    if cursor.kind == CursorKind.NAMESPACE:
        name_str = cursor.spelling
        is_anonymous = not name_str
        location_str = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location else "unknown"
        
        fqn = _generate_fqn(parent_fqn, name_str, is_anonymous, target_filepath, cursor.location)
        db_name = "(anonymous)" if is_anonymous else name_str

        new_ns_id = _get_or_create_namespace_db_entry(db_conn, db_cursor, fqn, db_name, current_ns_id, file_id, location_str)

        scope_stack.append((new_ns_id, fqn))
        for child in cursor.get_children():
            traverse_ast(child, db_conn, db_cursor, file_id, target_filepath, scope_stack)
        scope_stack.pop()
        return

    # Determine scope (file scope, class/struct scope, etc.)
    parent_kind, parent_name = None, None
    is_file_scope, is_class_scope = False, False
    semantic_parent = cursor.semantic_parent
    if semantic_parent:
        is_file_scope = semantic_parent.kind == CursorKind.TRANSLATION_UNIT or semantic_parent.kind == CursorKind.NAMESPACE
        is_class_scope = semantic_parent.kind in [CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL, CursorKind.NAMESPACE]
        parent_kind = semantic_parent.kind.name
        if is_class_scope:
            parent_name = semantic_parent.spelling
    
    # --- Handle definitions (mainly file scope or class scope) ---
    # マクロは名前空間に属さないので、従来通りの処理
    if cursor.kind == CursorKind.MACRO_DEFINITION:
        name = cursor.spelling
        body = get_macro_body(cursor)
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}"
        if name:
            db_cursor.execute("INSERT INTO macros (file_id, name, body, location) VALUES (?, ?, ?, ?)",
                              (file_id, name, body, location))

    elif cursor.kind == CursorKind.FUNCTION_DECL and (is_file_scope or is_class_scope):
        name = cursor.spelling
        if not name: return

        return_type = cursor.result_type.spelling
        params = get_function_params(cursor)
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}"
        is_definition = cursor.is_definition()
        is_static = cursor.storage_class == StorageClass.STATIC

        if not return_type and parent_kind in ['CLASS_DECL', 'STRUCT_DECL']:
             if name == parent_name: return_type = "(constructor)"
             elif name == f"~{parent_name}": return_type = "(destructor)"

        db_cursor.execute(
            "INSERT INTO functions (file_id, namespace_id, name, return_type, parameters, is_declaration, is_static, parent_kind, parent_name, location) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_id, current_ns_id, name, return_type, params, 0 if is_definition else 1, 1 if is_static else 0, parent_kind if is_class_scope else None, parent_name if is_class_scope else None, location)
        )

    elif cursor.kind == CursorKind.VAR_DECL and is_file_scope:
        name = cursor.spelling
        if not name: return

        var_type = cursor.type.spelling
        is_extern = cursor.storage_class == StorageClass.EXTERN
        is_static = cursor.storage_class == StorageClass.STATIC
        init = has_initializer(cursor)
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}"

        db_cursor.execute(
            "INSERT INTO variables (file_id, namespace_id, name, type, is_extern, is_static, has_initializer, location) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (file_id, current_ns_id, name, var_type, 1 if is_extern else 0, 1 if is_static else 0, init, location)
        )

    elif cursor.kind in [CursorKind.STRUCT_DECL, CursorKind.UNION_DECL] and is_file_scope and cursor.is_definition():
        kind = 'struct' if cursor.kind == CursorKind.STRUCT_DECL else 'union'
        name = cursor.spelling or None
        members = get_struct_union_members(cursor)
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}"
        db_cursor.execute("INSERT INTO structs_unions (file_id, namespace_id, kind, name, members, location) VALUES (?, ?, ?, ?, ?, ?)",
                          (file_id, current_ns_id, kind, name, members, location))

    elif cursor.kind == CursorKind.ENUM_DECL and is_file_scope and cursor.is_definition():
        name = cursor.spelling or None
        constants = get_enum_constants(cursor)
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}"
        db_cursor.execute("INSERT INTO enums (file_id, namespace_id, name, constants, location) VALUES (?, ?, ?, ?, ?)",
                          (file_id, current_ns_id, name, constants, location))

    elif cursor.kind == CursorKind.TYPEDEF_DECL and is_file_scope:
        name = cursor.spelling
        underlying_type = cursor.underlying_typedef_type.spelling
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}"
        db_cursor.execute("INSERT INTO typedefs (file_id, namespace_id, name, underlying_type, location) VALUES (?, ?, ?, ?, ?)",
                          (file_id, current_ns_id, name, underlying_type, location))

    # --- Recursively explore child nodes ---
    if cursor.kind != CursorKind.NAMESPACE:
        for child in cursor.get_children():
            traverse_ast(child, db_conn, db_cursor, file_id, target_filepath, scope_stack)

# --- メイン処理 ---
def main():
    parser = argparse.ArgumentParser(description='Parse C/C++ implementation file (.c, .cpp) and store definitions in SQLite.')
    parser.add_argument('source_file', help='Path to the C/C++ source file to parse.')
    parser.add_argument('-db', '--database', default='implementations.db', help='Path to the SQLite database file (default: implementations.db).')
    parser.add_argument('-I', '--include', action='append', default=[], help='Add directory to include search path.')
    parser.add_argument('-D', '--define', action='append', default=[], help='Define a macro.')
    parser.add_argument('--libclang', help=f'Path to libclang library file (e.g., {LIBCLANG_PATH or "/path/to/libclang.so"})')
    parser.add_argument('--lang', choices=['c', 'c++'], default=None, help='Force language.')
    parser.add_argument('--std', default=None, help='Set C/C++ standard.')

    args = parser.parse_args()

    source_filepath = args.source_file
    db_filepath = args.database
    clang_args = [f'-I{d}' for d in args.include] + [f'-D{m}' for m in args.define]

    language = args.lang
    if not language:
        if source_filepath.endswith(('.cpp', '.cxx', '.cc', '.C')):
            language = 'c++'
        else:
            language = 'c'

    if language == 'c++':
        clang_args.extend(['-x', 'c++'])
        std_arg = args.std or 'c++11'
        clang_args.append(f'-std={std_arg}')
    else:
        clang_args.extend(['-x', 'c'])
        if args.std: clang_args.append(f'-std={args.std}')

    print(f"Parsing: {source_filepath}")
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
        # 実装ファイルなので PARSE_SKIP_FUNCTION_BODIES は使用しない
        tu = index.parse(source_filepath, args=clang_args, options=0)

        if any(d.severity >= d.Error for d in tu.diagnostics):
            print("Errors occurred during parsing. Results might be incomplete.", file=sys.stderr)
            for diag in tu.diagnostics:
                if diag.severity >= diag.Warning:
                    print(f"  {diag.severity.name}: {diag.spelling} at {diag.location}", file=sys.stderr)

        conn = setup_database(db_filepath)
        db_cursor = conn.cursor()

        target_filepath_abs = os.path.abspath(source_filepath)
        file_id = add_file_record(conn, source_filepath)

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
         print(f"Error: Source file not found: {source_filepath}", file=sys.stderr)
         sys.exit(1)
    except sqlite3.Error as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        if 'library file' in str(e):
             print("Error: Failed to find or load libclang library. Please ensure LLVM/Clang is installed and accessible.", file=sys.stderr)
             print("You might need to set the LIBCLANG_PATH variable in the script or use the --libclang argument.", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()