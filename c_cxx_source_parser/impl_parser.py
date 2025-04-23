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

    # ファイル管理テーブル (ヘッダと同じ)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE NOT NULL,
            last_parsed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # マクロ定義テーブル (ヘッダと同じ - .c/.cpp内で定義されるマクロ用)
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

    # 関数定義/宣言テーブル (拡張)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS functions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            return_type TEXT,
            parameters TEXT,
            is_declaration INTEGER NOT NULL, -- 0: 定義(本体あり), 1: 宣言(プロトタイプ)
            is_static INTEGER DEFAULT 0,     -- 1: static関数, 0: その他
            parent_kind TEXT,                -- C++用: 親カーソルの種類 (例: CLASS_DECL)
            parent_name TEXT,                -- C++用: 親クラス/構造体名
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_func_name ON functions (name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_func_parent ON functions (parent_name)')


    # 構造体/共用体定義テーブル (ヘッダと同じ - .c/.cpp内で定義される場合用)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS structs_unions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            kind TEXT NOT NULL, -- 'struct' or 'union'
            name TEXT,
            members TEXT,
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_struct_name ON structs_unions (name)')


    # 列挙型定義テーブル (ヘッダと同じ - .c/.cpp内で定義される場合用)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS enums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            name TEXT,
            constants TEXT,
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_enum_name ON enums (name)')


    # Typedef定義テーブル (ヘッダと同じ - .c/.cpp内で定義される場合用)
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


    # グローバル/静的変数 定義/宣言テーブル (拡張)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS variables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            type TEXT,
            is_extern INTEGER DEFAULT 0,     -- 1: extern宣言, 0: その他(定義の可能性)
            is_static INTEGER DEFAULT 0,     -- 1: static変数, 0: その他
            has_initializer INTEGER DEFAULT 0, -- 1: 初期化子を持つ, 0: 持たない (簡易チェック)
            location TEXT,
            FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_var_name ON variables (name)')


    conn.commit()
    return conn

# ヘッダ解析と同じ関数 (変更なし)
def clear_definitions_for_file(conn, file_id):
    cursor = conn.cursor()
    # 注意: テーブル名は更新されたスキーマに合わせてください
    tables = ['macros', 'functions', 'structs_unions', 'enums', 'typedefs', 'variables']
    for table in tables:
        cursor.execute(f"DELETE FROM {table} WHERE file_id = ?", (file_id,))
    conn.commit()

# ヘッダ解析と同じ関数 (変更なし)
def add_file_record(conn, filepath):
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

# ヘッダ解析と同じ関数 (変更なし)
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

# ヘッダ解析と同じ関数 (変更なし)
def get_function_params(cursor):
    params = []
    # get_arguments() は宣言に対して有効。定義の場合は型情報を辿る必要がある場合も。
    # libclangがうまく取れない場合、cursor.type.argument_types() なども試せるが複雑化する。
    # ここでは get_arguments() がうまく機能することを期待する。
    try:
        for arg in cursor.get_arguments():
             # 引数名がない場合もある (例: void func(int);)
            param_name = arg.spelling or ""
            param_type = arg.type.spelling
            params.append(f"{param_type} {param_name}".strip())
    except Exception as e:
        print(f"Warning: Could not get arguments for {cursor.spelling}: {e}", file=sys.stderr)
        # 型情報からパラメータを取得するフォールバック (より複雑)
        try:
             func_type = cursor.type
             if func_type.kind == TypeKind.FUNCTIONPROTO or func_type.kind == TypeKind.FUNCTIONNOPROTO:
                 arg_types = func_type.argument_types()
                 for i, arg_type in enumerate(arg_types):
                     params.append(f"{arg_type.spelling} arg{i+1}") # 仮の引数名
        except Exception as e2:
             print(f"Warning: Could not get argument types for {cursor.spelling}: {e2}", file=sys.stderr)
             return "..." # 取得失敗を示す

    return ", ".join(params)


# ヘッダ解析と同じ関数 (変更なし)
def get_struct_union_members(cursor):
    members = []
    for child in cursor.get_children():
        if child.kind == CursorKind.FIELD_DECL:
            members.append(f"{child.type.spelling} {child.spelling};")
    return " ".join(members)

# ヘッダ解析と同じ関数 (変更なし)
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
    # VAR_DECL の子は型(TYPE_REFなど)と初期化式(INTEGER_LITERAL, CALL_EXPRなど)になる
    for child in cursor.get_children():
        # 代表的な初期化式の種類をチェック (網羅的ではない可能性あり)
        if child.kind in [
            CursorKind.INTEGER_LITERAL, CursorKind.FLOATING_LITERAL,
            CursorKind.IMAGINARY_LITERAL, CursorKind.STRING_LITERAL,
            CursorKind.CHARACTER_LITERAL, CursorKind.CXX_BOOL_LITERAL_EXPR,
            CursorKind.CXX_NULL_PTR_LITERAL_EXPR, CursorKind.GNU_NULL_EXPR,
            CursorKind.UNEXPOSED_EXPR, # 初期化式が複雑な場合これになることも
            CursorKind.CALL_EXPR, CursorKind.INIT_LIST_EXPR,
            CursorKind.PAREN_EXPR, CursorKind.UNARY_OPERATOR,
            CursorKind.BINARY_OPERATOR,
            CursorKind.CONDITIONAL_OPERATOR, CursorKind.CSTYLE_CAST_EXPR,
            CursorKind.CXX_STATIC_CAST_EXPR, CursorKind.CXX_DYNAMIC_CAST_EXPR,
            CursorKind.CXX_REINTERPRET_CAST_EXPR, CursorKind.CXX_CONST_CAST_EXPR,
            CursorKind.CXX_FUNCTIONAL_CAST_EXPR, CursorKind.CXX_NEW_EXPR,
            CursorKind.CXX_DELETE_EXPR, CursorKind.CXX_THIS_EXPR,
            CursorKind.ADDR_LABEL_EXPR, CursorKind.StmtExpr, # GCC拡張
            CursorKind.COMPOUND_LITERAL_EXPR # C99複合リテラル
        ]:
            return 1
    return 0


# --- Clang AST 解析 (実装ファイル向けに更新) ---

def traverse_ast(cursor, db_conn, file_id, target_filepath):
    """ASTを再帰的に走査し、定義をデータベースに追加する"""
    db_cursor = db_conn.cursor()

    # 対象ファイル内のカーソルか、関連するヘッダのカーソルかを判定
    in_target_file = False
    if cursor.location and cursor.location.file:
        try:
            in_target_file = os.path.abspath(cursor.location.file.name) == target_filepath
        except FileNotFoundError:
            # 一時ファイルなどでエラーになる場合がある
             pass
    
    # スコープの取得 (ファイルスコープか、クラス/構造体スコープかなど)
    parent_kind = None
    parent_name = None
    is_file_scope = False
    is_class_scope = False # C++用

    try:
        semantic_parent = cursor.semantic_parent
        if semantic_parent:
            parent_kind_enum = semantic_parent.kind
            parent_kind = parent_kind_enum.name # 文字列として保持
            is_file_scope = parent_kind_enum == CursorKind.TRANSLATION_UNIT
            is_class_scope = parent_kind_enum in [CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL, CursorKind.NAMESPACE] # C++
            if is_class_scope and semantic_parent.spelling:
                 parent_name = semantic_parent.spelling
    except Exception as e:
        # print(f"Debug: Could not get semantic parent for {cursor.kind} {cursor.spelling}: {e}", file=sys.stderr)
        pass # 親が取れない場合もある


    # --- 定義の処理 (ファイルスコープ or クラススコープを中心に) ---

    # マクロ (ファイル内で定義されたもの)
    # cursor.location がないとファイル判定できないのでチェック
    if cursor.kind == CursorKind.MACRO_DEFINITION and in_target_file:
        name = cursor.spelling
        body = get_macro_body(cursor)
        location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}"
        if name:
            db_cursor.execute("INSERT INTO macros (file_id, name, body, location) VALUES (?, ?, ?, ?)",
                              (file_id, name, body, location))

    # 関数 (ファイルスコープ or クラススコープ)
    elif cursor.kind == CursorKind.FUNCTION_DECL and (is_file_scope or is_class_scope):
         # .cppファイル等では、ヘッダで宣言され .cpp で定義される場合、両方のファイルで現れる。
         # in_target_file で現在のファイルでの定義/宣言に絞る。
        if in_target_file:
            name = cursor.spelling
            if not name: # 無名関数などはスキップ
                return

            return_type = cursor.result_type.spelling
            params = get_function_params(cursor)
            location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}"
            is_definition = cursor.is_definition() # 本体があるか (定義か)
            storage_class = cursor.storage_class
            is_static = storage_class == StorageClass.STATIC

            # C++のコンストラクタ/デストラクタ等は戻り値型がない場合がある
            if not return_type and parent_kind in ['CLASS_DECL', 'STRUCT_DECL']:
                 if name == parent_name: # コンストラクタ
                     return_type = "(constructor)"
                 elif name == f"~{parent_name}": # デストラクタ
                     return_type = "(destructor)"

            # print(f" Found Function: {name} (static={is_static}, def={is_definition}) in {parent_kind}:{parent_name} at {location}")
            db_cursor.execute(
                "INSERT INTO functions (file_id, name, return_type, parameters, is_declaration, is_static, parent_kind, parent_name, location) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (file_id, name, return_type, params, 0 if is_definition else 1, 1 if is_static else 0, parent_kind if is_class_scope else None, parent_name if is_class_scope else None, location)
            )

    # グローバル変数 / ファイル静的変数 (ファイルスコープのみ)
    elif cursor.kind == CursorKind.VAR_DECL and is_file_scope:
        if in_target_file:
            name = cursor.spelling
            if not name: # 無名変数はスキップ
                return

            var_type = cursor.type.spelling
            storage_class = cursor.storage_class
            is_extern = storage_class == StorageClass.EXTERN
            is_static = storage_class == StorageClass.STATIC
            init = has_initializer(cursor)
            location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}"

            # print(f" Found Variable: {name} (static={is_static}, extern={is_extern}, init={init}) at {location}")
            db_cursor.execute(
                "INSERT INTO variables (file_id, name, type, is_extern, is_static, has_initializer, location) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (file_id, name, var_type, 1 if is_extern else 0, 1 if is_static else 0, init, location)
            )

    # 構造体/共用体 (ファイルスコープ) - ヘッダでの定義が多いが、.c/.cpp 内定義も考慮
    elif cursor.kind == CursorKind.STRUCT_DECL and is_file_scope:
        if in_target_file and cursor.is_definition(): # 定義のみ記録
            name = cursor.spelling or None
            kind = 'struct'
            members = get_struct_union_members(cursor)
            location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}"
            db_cursor.execute("INSERT INTO structs_unions (file_id, kind, name, members, location) VALUES (?, ?, ?, ?, ?)",
                              (file_id, kind, name, members, location))

    elif cursor.kind == CursorKind.UNION_DECL and is_file_scope:
        if in_target_file and cursor.is_definition():
            name = cursor.spelling or None
            kind = 'union'
            members = get_struct_union_members(cursor)
            location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}"
            db_cursor.execute("INSERT INTO structs_unions (file_id, kind, name, members, location) VALUES (?, ?, ?, ?, ?)",
                              (file_id, kind, name, members, location))

    # 列挙型 (ファイルスコープ)
    elif cursor.kind == CursorKind.ENUM_DECL and is_file_scope:
        if in_target_file and cursor.is_definition():
            name = cursor.spelling or None
            constants = get_enum_constants(cursor)
            location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}"
            db_cursor.execute("INSERT INTO enums (file_id, name, constants, location) VALUES (?, ?, ?, ?)",
                              (file_id, name, constants, location))

    # Typedef (ファイルスコープ)
    elif cursor.kind == CursorKind.TYPEDEF_DECL and is_file_scope:
        if in_target_file:
            name = cursor.spelling
            try:
                 underlying_type = cursor.underlying_typedef_type.spelling
            except Exception:
                 underlying_type = "unknown" # underlying typeが取得できないケースへの対処
            location = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}"
            db_cursor.execute("INSERT INTO typedefs (file_id, name, underlying_type, location) VALUES (?, ?, ?, ?)",
                              (file_id, name, underlying_type, location))


    # --- 子ノードを再帰的に探索 ---
    # 関数やクラスの「中」は基本的に追わない設定 (ファイル/クラススコープの定義が主目的のため)
    # ただし、Namespace など、さらに掘り下げるべきケースもある。
    # ここでは、すべてのトップレベルの子ノードを辿るシンプルな実装とする。
    # (関数の中のローカル変数などは、上記の is_file_scope/is_class_scope チェックで除外される想定)
    for child in cursor.get_children():
        traverse_ast(child, db_conn, file_id, target_filepath)


# --- メイン処理 ---
def main():
    parser = argparse.ArgumentParser(description='Parse C/C++ implementation file (.c, .cpp) and store definitions in SQLite.')
    parser.add_argument('source_file', help='Path to the C/C++ source file to parse.')
    # デフォルトDB名を変更
    parser.add_argument('-db', '--database', default='implementations.db', help='Path to the SQLite database file (default: implementations.db).')
    parser.add_argument('-I', '--include', action='append', default=[], help='Add directory to include search path (crucial for resolving types).')
    parser.add_argument('-D', '--define', action='append', default=[], help='Define a macro (e.g., -DNDEBUG).')
    parser.add_argument('--libclang', help=f'Path to libclang library file (e.g., {LIBCLANG_PATH or "/path/to/libclang.so"})')
    parser.add_argument('--lang', choices=['c', 'c++'], default=None, help='Force language standard (e.g., c++11). Tries to guess from extension if not provided.')
    parser.add_argument('--std', default=None, help='Set C/C++ standard (e.g., c11, c++17).')


    args = parser.parse_args()

    source_filepath = args.source_file
    db_filepath = args.database
    clang_args = []

    # インクルードパス (実装ファイル解析では特に重要)
    for include_dir in args.include:
        clang_args.append(f'-I{include_dir}')

    # マクロ定義
    for define_macro in args.define:
        clang_args.append(f'-D{define_macro}')

    # 言語と標準
    language = args.lang
    if not language:
        if source_filepath.endswith(('.cpp', '.cxx', '.cc', '.C')):
            language = 'c++'
        else:
            language = 'c' # デフォルト C

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


    print(f"Parsing: {source_filepath}")
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
    else:
         print("Attempting to find libclang automatically...")

    try:
        # Clangインデックス作成
        index = Index.create()

        # 実装ファイルをパース
        # PARSE_SKIP_FUNCTION_BODIES を *削除* して is_definition() の精度を上げる
        # (本体の内容自体はDBに保存しないが、定義かどうかの判定に使う)
        parse_options = 0
        print("Parsing source file (this may take a moment)...")
        tu = index.parse(
            source_filepath,
            args=clang_args,
            options=parse_options
        )

        # パースエラーチェック
        has_errors = False
        for diag in tu.diagnostics:
             # Warning 以上を表示 (Info, Ignored は除外)
            if diag.severity >= diag.Warning:
                severity_str = {
                    diag.Ignored: "Ignored", diag.Note: "Note", diag.Warning: "Warning",
                    diag.Error: "Error", diag.Fatal: "Fatal"
                }.get(diag.severity, "Unknown")
                loc = diag.location
                loc_str = f"{loc.file}:{loc.line}:{loc.column}" if loc and loc.file else "(no location)"
                print(f"Parse {severity_str}: {diag.spelling} at {loc_str}", file=sys.stderr)
                if diag.severity >= diag.Error:
                    has_errors = True

        if has_errors:
            print("Errors occurred during parsing. Results might be incomplete.", file=sys.stderr)
            # エラーがあっても続行する

        # データベース接続とセットアップ
        conn = setup_database(db_filepath)

        # ファイルレコードを追加/更新し、ファイルIDを取得
        target_filepath_abs = os.path.abspath(source_filepath)
        file_id = add_file_record(conn, source_filepath)

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
         print(f"Error: Source file not found: {source_filepath}", file=sys.stderr)
         sys.exit(1)
    except sqlite3.Error as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        if 'library file' in str(e) or 'libclang' in str(e):
             print("Error: Failed to find or load libclang library.", file=sys.stderr)
             print("Please ensure LLVM/Clang is installed and accessible.", file=sys.stderr)
             print("You might need to set the LIBCLANG_PATH variable in the script or use the --libclang argument.", file=sys.stderr)
        import traceback
        traceback.print_exc() # 詳細なスタックトレースを表示
        sys.exit(1)


if __name__ == "__main__":
    main()
