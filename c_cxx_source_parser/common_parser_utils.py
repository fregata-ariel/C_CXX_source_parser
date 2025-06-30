import os
import sqlite3
import time
from clang.cindex import CursorKind
import clang.cindex
from dataclasses import dataclass

@dataclass
class ParserContext:
    parser_type: str  # 'header' or 'impl'

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

def get_global_namespace_id(db_cursor):
    """グローバル名前空間のIDを取得する"""
    db_cursor.execute("SELECT id FROM namespaces WHERE full_qualified_name = ?", ("(global)",))
    result = db_cursor.fetchone()
    if not result:
        raise Exception("Global namespace not found in the database. setup_database might have failed.")
    return result[0]

def generate_fqn(parent_fqn, current_name_str, is_anonymous, file_path, location):
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

def get_or_create_namespace_db_entry(db_conn, db_cursor, fqn, name_for_db, parent_db_id, file_id, location_str):
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

def has_initializer(cursor):
    """変数が初期化子を持つか簡易的にチェック"""
    for child in cursor.get_children():
        if child.kind.is_expression() or child.kind == CursorKind.INIT_LIST_EXPR:
            return 1
    return 0

def setup_common_tables(conn: sqlite3.Connection):
    """
    全てのパーサーで共通のデータベーステーブルと初期レコードをセットアップする。
    - files テーブル
    - namespaces テーブル
    - macros テーブル
    - structs_unions テーブル
    - enums テーブル
    - typedefs テーブル
    """
    cursor = conn.cursor()

    # 1. ファイル管理テーブルを作成
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE NOT NULL,
            last_parsed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 2. グローバルコンテキスト用のダミーファイルレコードを挿入し、そのIDを取得
    #    これは、特定のファイルに依存しない (global) 名前空間の file_id として使用する。
    global_context_filepath = '(global_context)'
    cursor.execute("INSERT OR IGNORE INTO files (filepath) VALUES (?)", (global_context_filepath,))
    cursor.execute("SELECT id FROM files WHERE filepath = ?", (global_context_filepath,))
    global_file_id_record = cursor.fetchone()
    if not global_file_id_record:
        # 通常、上記のINSERT OR IGNOREでレコードは保証されるはず
        raise Exception("Fatal: Failed to insert or find the '(global_context)' record in the files table.")
    global_file_id = global_file_id_record[0]

    # 3. 名前空間テーブルを作成
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

    # 4. グローバル名前空間の初期レコードを挿入
    cursor.execute("""
        INSERT OR IGNORE INTO namespaces (name, parent_namespace_id, file_id, location, full_qualified_name)
        VALUES (?, NULL, ?, ?, ?)
    """, ("(global)", global_file_id, "N/A", "(global)"))

    # 5. 共通の定義テーブルを作成 (すべて namespace_id を含む)

    # マクロ定義テーブル (namespace_id は不要)
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

    # 6. データベースへの変更をコミット
    conn.commit()

