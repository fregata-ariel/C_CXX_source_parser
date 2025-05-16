# `namespace`v1実装までの会話

## 実装計画草案

---

> me  

あなたには名前空間をデータベースに収容するためのスキーマと，パーサーを考えてもらいます．先ずどのような方法で名前空間ごとにそれぞれの要素が定義されているかくべするために必要なことなどを含めてステップバイステップで考えてください．

---

> gemini 2.5 pro preview

了解しました。C++の名前空間 (`namespace`) を解析し、データベースに情報を格納するための機能追加について、ステップバイステップで考察します。

### Step 1: 名前空間の特性と記録すべき情報

まず、名前空間のどのような情報を記録する必要があるかを考えます。

1.  **名前空間自体の定義:**
    * どの名前空間が存在するか？ (例: `std`, `MyLib`, `MyLib::Utils`)
    * 名前空間の名前は何か？ (匿名名前空間 `namespace { ... }` も区別する必要がある)
    * 名前空間はネストしているか？ 親の名前空間は何か？ (例: `Utils` の親は `MyLib`)
    * どこで定義されているか？ (ファイルパスと行番号)

2.  **名前空間に属する要素:**
    * 各関数、変数、クラス、構造体、列挙型、typedef がどの名前空間に属しているか？
    * グローバルスコープ（どの名前空間にも属さない）の要素も区別する必要がある。

### Step 2: データベーススキーマの設計

既存のスキーマを拡張して名前空間を表現する方法を検討します。新しいテーブルを追加するアプローチが最も柔軟性が高いでしょう。

1.  **新しい `namespaces` テーブルの作成:**
    * 名前空間自体の情報を格納するためのテーブルです。
    * **カラム案:**
        * `id` (INTEGER PRIMARY KEY AUTOINCREMENT): 名前空間レコードの一意なID。
        * `name` (TEXT): 名前空間の名前。`std`, `MyLib` など。
            * **匿名名前空間:** 特定の識別子（例: `(anonymous)`）を使用するか、`name` を NULL にしてフラグ (`is_anonymous` INTEGER) を持たせるか。`(anonymous)` のような名前を使う方がシンプルかもしれません。
            * **グローバルスコープ:** これも特別なレコードとして扱う必要があり、`id=1, name='(global)'` のようなレコードを初期データとして持つと便利です。
        * `parent_namespace_id` (INTEGER, FOREIGN KEY REFERENCES namespaces(id) ON DELETE CASCADE): 親の名前空間の `id`。トップレベル（グローバル直下）の名前空間やグローバルスコープ自体は `NULL`。これによりネスト構造を表現します。
        * `file_id` (INTEGER NOT NULL, FOREIGN KEY REFERENCES files(id) ON DELETE CASCADE): この名前空間定義が**最初に見つかった**ファイルのID。分割定義を考慮すると少し曖昧になる可能性がありますが、主要な定義場所を示す指標にはなります。
        * `location` (TEXT): 名前空間定義 (`namespace N { ...`) が開始されるソースコード上の位置 (`filename:line:column`)。
        * `full_qualified_name` (TEXT UNIQUE): 完全修飾名 (例: `MyLib::Utils`, `MyLib::(anonymous)`, `(global)` など)。これを UNIQUE にすることで、同じ名前空間が異なるファイルで定義されても（分割定義）、DB内で一意に識別しやすくなります。ただし、これを正確に生成・管理するロジックがパーサー側で必要になります。

2.  **既存の定義テーブルへの参照追加:**
    * 以下のテーブルに `namespace_id` カラムを追加します。
        * `functions`
        * `variables`
        * `structs_unions`
        * `enums`
        * `typedefs`
    * **カラム定義:** `namespace_id` INTEGER, FOREIGN KEY REFERENCES `namespaces`(`id`) ON DELETE CASCADE
    * この `namespace_id` は、その要素が属する名前空間を示します。グローバルスコープに属する場合は、`(global)` スコープに対応する `namespaces.id` (例: 1) を格納します。
    * **注意:** マクロ (`macros` テーブル) はプリプロセッサによって処理され、C++の名前空間スコープに属さないため、`namespace_id` は不要です。

### Step 3: パーサー (`header_parser.py`, `impl_parser.py`) の改修計画

Clang の AST (Abstract Syntax Tree) を走査して名前空間情報を抽出・記録するようにパーサーを改修します。

1.  **AST走査の準備:**
    * `traverse_ast` 関数（またはそれを呼び出す関数）で、現在の名前空間スコープを追跡するためのメカニズムを導入します。ネストに対応するため、現在の `namespace_id` を保持するスタックが適しています。
    * 走査開始時、スタックにはグローバルスコープの `namespace_id` (例: 1) のみを入れておきます。

2.  **名前空間定義 (`CursorKind.NAMESPACE`) の処理:**
    * `traverse_ast` 内で `cursor.kind == CursorKind.NAMESPACE` を検出します。
    * **名前空間名の取得:** `cursor.spelling` で名前を取得します。
    * **匿名判定:** `cursor.displayname == ""` や `cursor.is_anonymous()` (利用可能なら) で匿名名前空間かどうかを判定します。
    * **親IDの取得:** 現在のスコープスタックのトップにある `namespace_id` を親IDとして取得します。
    * **完全修飾名の生成:** 親の名前空間の `full_qualified_name` と現在の名前空間名を結合して生成します (例: 親が `MyLib` で現在が `Utils` なら `MyLib::Utils`)。匿名の場合は `Parent::(anonymous)` のようにします。
    * **データベースへの登録:**
        * `full_qualified_name` をキーにして `namespaces` テーブルを検索します。
        * 存在しなければ、新しいレコードとして `name`, `parent_namespace_id`, `file_id`, `location`, `full_qualified_name` を挿入し、新しい `id` を取得します。
        * 存在すれば、その `id` を使用します。（`location` や `file_id` は最初に見つかったものを保持するか、更新するかは設計次第ですが、通常は最初のもので良いでしょう）。
    * **スコープの更新:** 取得した `namespace_id` をスコープスタックにプッシュします。
    * **子の走査:** `cursor.get_children()` で取得した子ノードに対して `traverse_ast` を再帰呼び出しします。
    * **スコープの復元:** その名前空間ノードのすべての子の走査が終わったら、スコープスタックから現在の `namespace_id` をポップします。

3.  **各種定義要素の処理:**
    * `FUNCTION_DECL`, `VAR_DECL`, `STRUCT_DECL`, `UNION_DECL`, `ENUM_DECL`, `TYPEDEF_DECL` などを検出した際、現在のスコープスタックのトップにある `namespace_id` を取得します。
    * 各テーブル (`functions`, `variables` など) にデータを挿入する際に、この取得した `namespace_id` も一緒にカラムに含めます。

4.  **グローバルスコープ要素の処理:**
    * `traverse_ast` の開始時（またはトップレベルの要素を処理する際）、スコープスタックにはグローバルスコープの `namespace_id` (例: 1) が入っています。名前空間定義 (`NAMESPACE`) に入る前に検出された要素は、自動的にこのグローバルスコープの `namespace_id` が割り当てられます。

### Step 4: 実装とテスト

1.  **`setup_database` 関数の更新:** 新しい `namespaces` テーブルの CREATE 文と、既存テーブルへの `namespace_id` カラム追加の ALTER 文 (または CREATE 文の修正) を追加します。グローバルスコープ用レコードの初期挿入処理も追加します。
2.  **`traverse_ast` 関数の修正:** 上記 Step 3 のロジックを実装します。スコープスタックの管理、`NAMESPACE` カーソルの処理、各定義要素への `namespace_id` 割り当てを行います。
3.  **テストケースの作成:**
    * グローバルスコープの要素を持つコード。
    * 単純な名前空間を持つコード。
    * ネストした名前空間を持つコード。
    * 匿名名前空間を持つコード。
    * 同じ名前空間が複数のファイルで定義されるコード (分割定義)。
4.  **テストの実行:** 作成したテストケースでパーサーを実行し、`namespaces` テーブルと各定義テーブルの `namespace_id` が正しく格納されているかを確認するテストを作成・実行します。

このステップバイステップのアプローチにより、C++の名前空間を体系的に解析し、データベースに格納する機能を実装できるはずです。

---

## 実装計画書作成

### `Step 1: 名前空間の特性と記録すべき情報`に関して

> me  

`Step 1: 名前空間の特性と記録すべき情報`に関して，実装する手前までくらいの具体的な内容の計画書をステップバイステップで書いてください．

> gemini 2.5 pro preview

はい、承知いたしました。「Step 1: 名前空間の特性と記録すべき情報」について、実装手前までの具体的な計画書をステップバイステップで記述します。

---

## 名前空間情報の記録に関する計画書 (Step 1 詳細)

### 1. 目的とスコープ

**1.1. 目的**
C++ソースコードにおける名前空間の定義と、各プログラム要素（関数、変数、型定義など）がどの名前空間に属しているかを正確に識別し、データベースに記録可能にすること。これにより、コードの構造理解、シンボル解決の補助、リファクタリング支援などの基盤情報とする。

**1.2. 今回のスコープ**
* **記録対象:**
    * 名前付き名前空間 (`namespace MyNamespace { ... }`)
    * ネストした名前空間 (`namespace N1 { namespace N2 { ... } }`)
    * 匿名名前空間 (`namespace { ... }`)
    * グローバル名前空間 (明示的な `namespace` キーワードがないトップレベルスコープ)
    * 上記名前空間内に直接定義された要素 (関数、変数、クラス/構造体/共用体、列挙型、typedef) とその所属関係。
* **スコープ外 (今回は記録しない or 限定的な扱い):**
    * `using`宣言 (`using std::cout;`) および `using`ディレクティブ (`using namespace std;`) の詳細な影響範囲の追跡。これらは名前解決に使われるが、定義そのものではないため、今回の主眼ではない。
    * 名前空間エイリアス (`namespace Alias = N1::N2;`) の定義と解決。
    * インライン名前空間 (`inline namespace`) の特殊な振る舞い（メンバーが親名前空間のメンバーとしても扱われる点）の完全なエミュレーション。インライン名前空間自体は通常の名前空間として定義を記録するが、「インラインである」という属性は今回必須とはしない（将来的な拡張は可能）。
    * 複数の翻訳単位 (.cppファイル) にまたがる名前空間定義の完全な統合。パーサーはファイルごとに実行されるため、各ファイル内で観測された名前空間情報を記録する。データベースレベルでの統合は別課題とする。

### 2. 記録対象となる「名前空間」の定義と属性

**2.1. 名前空間エンティティの定義**
ソースコード上で `namespace Name { ... }` または `namespace { ... }` によって定義されるスコープ、および暗黙的に存在するグローバルスコープを「名前空間エンティティ」として扱う。

**2.2. 名前空間エンティティの必須属性**
各名前空間エンティティについて、以下の情報を記録する。

* **2.2.1. 一意識別子 (ID)**
    * 内容: データベース内で各名前空間エンティティを一意に識別するためのID。
    * データ型: INTEGER (PRIMARY KEY, AUTOINCREMENT)
* **2.2.2. 名前 (Name)**
    * 内容: 名前空間の名前。
    * データ型: TEXT
    * 例: `"MyNamespace"`, `"N2"`
    * 特殊ケース:
        * **匿名名前空間の場合:** `"(anonymous)"` のような予約文字列を格納する。ファイルごとに一意性を保つため、実際にはパーサー実行時に内部的に生成される一意な名前（例: `"(anonymous)_fileId_count"`）や、FQNで区別する。
        * **グローバル名前空間の場合:** `"(global)"` という予約文字列を格納する。
* **2.2.3. 親名前空間ID (Parent Namespace ID)**
    * 内容: ネストしている場合、直接の親となる名前空間エンティティのID。
    * データ型: INTEGER (NULL許容, 外部キーとして自身のテーブルのIDを参照)
    * 特殊ケース:
        * **グローバル名前空間の場合:** `NULL` とする。
        * **グローバル直下の名前空間の場合:** グローバル名前空間のIDを参照する。
* **2.2.4. 定義ファイルID (File ID)**
    * 内容: この名前空間定義が出現したソースファイルのID (`files`テーブルのID)。
    * データ型: INTEGER (NOT NULL, 外部キーとして`files.id`を参照)
* **2.2.5. 定義位置 (Location)**
    * 内容: 名前空間定義 (`namespace Name {` の開始位置) のファイル名、行番号、カラム番号。
    * データ型: TEXT
    * フォーマット: `"filename:line:column"`
* **2.2.6. 完全修飾名 (Full Qualified Name - FQN)**
    * 内容: グローバル名前空間からのパスを含む一意な名前。
    * データ型: TEXT (UNIQUE制約を検討)
    * 生成ルール:
        * グローバル名前空間: `"(global)"`
        * トップレベル名前空間 `N`: `"(global)::N"`
        * ネストした名前空間 `M` (親が `"(global)::N"`): `"(global)::N::M"`
        * 匿名名前空間 (親が `"(global)::N"`): `"(global)::N::(anonymous)_suffix"` のような形式。suffix はファイル内での出現順などで一意にする。
    * 役割: 異なるファイルや異なる親の下にある同名の名前空間を区別する。データベース内で名前空間を一意に参照する際のキーとなる。

**2.3. グローバルスコープの扱い**
* 明示的な名前空間宣言の外側はすべてグローバルスコープとして扱う。
* `namespaces` テーブルには、例えば `id=1`, `name='(global)'`, `parent_namespace_id=NULL`, `file_id=NULL` (または特別なファイルID), `location='N/A'`, `full_qualified_name='(global)'` のような特別なレコードをあらかじめ用意するか、パーサーが最初に処理するファイルで動的に作成する。

**2.4. 匿名名前空間の扱い**
* Clang AST上で `cursor.is_anonymous()` (または `cursor.displayname == ""` など) で識別する。
* `name` 属性には `"(anonymous)"` のような固定文字列を使用し、FQNで一意性を担保する。例えば、同じファイル内で複数の匿名名前空間がある場合は、`FQN` の末尾にインデックスやハッシュを追加する（例: `ParentFQN::(anonymous)_1`, `ParentFQN::(anonymous)_2`）。
* 定義位置が異なる匿名名前空間は、別個のエンティティとして記録される。

**2.5. ネストした名前空間の扱い**
* `parent_namespace_id` 属性を使用して階層構造を表現する。
* FQN は親のFQNをプレフィックスとして構築される (例: `ParentFQN::ChildName`)。

**2.6. 分割定義された名前空間の扱い (同一ファイル内および複数ファイル間)**
* **方針:** FQN (`full_qualified_name`) が同じであれば、それは論理的に同一の名前空間と見なす。
* **`namespaces` テーブルへの登録:**
    * 最初の出現時にFQNに基づいて `namespaces` テーブルにレコードを作成する。
    * 以降、同じFQNを持つ名前空間定義が見つかった場合、既存のレコードを使用する。`location` や `file_id` は、最初の定義を保持するか、あるいは別途「定義箇所リスト」のような関連テーブルで管理するか検討が必要。今回は**最初の定義のみを代表**として記録する方針とする。これにより `namespaces` テーブルの `file_id` と `location` は「このFQNの名前空間が最初に出現した場所」を指すことになる。
* **パーサーの役割:** AST走査中にFQNを正確に生成し、データベースに問い合わせて既存IDを取得するか、新規登録するかを判断する。

### 3. 記録対象となる「名前空間内の要素」と名前空間との関連付け

**3.1. 対象要素**
既存のパーサーが記録対象としている以下の要素。
* 関数 (`functions` テーブル)
* 変数 (`variables` テーブル)
* クラス、構造体、共用体 (`structs_unions` テーブル)
* 列挙型 (`enums` テーブル)
* Typedef (`typedefs` テーブル)

**3.2. 関連付け方法**
上記各要素のテーブルに、新たに `namespace_id` (INTEGER) カラムを追加する。
* この `namespace_id` は、その要素が属する `namespaces` テーブルのレコードの `id` を指す外部キーとする。
* 要素がグローバルスコープに属する場合、グローバル名前空間に対応する `namespaces.id` (例: 1) を格納する。

### 4. Clang Libtooling を用いた情報抽出戦略 (概要)

* **4.1. `CursorKind.NAMESPACE` の検出:**
    * AST走査中に `clang.cindex.CursorKind.NAMESPACE` を持つカーソルを処理対象とする。
    * `cursor.spelling` から名前空間名を取得。
    * `cursor.displayname == ""` や `cursor.is_anonymous()` で匿名性を判断。
    * `cursor.location` から定義位置を取得。
* **4.2. スコープスタックを用いた現在の名前空間の追跡:**
    * `traverse_ast` 関数内で、現在処理中の名前空間のID (`namespaces.id`) を保持するスタックを管理する。
    * `NAMESPACE` カーソルに入る際に、対応する `namespaces.id` をスタックにプッシュ。
    * `NAMESPACE` カーソルから出る際にスタックからポップ。
    * 要素（関数、変数など）の定義を検出した際は、スタックのトップにある `namespace_id` をその要素が属する名前空間のIDとして使用する。
* **4.3. FQN の動的生成ロジック:**
    * `NAMESPACE` カーソルを処理する際、親のFQN (スコープスタックの一つ前のレベルのFQN、あるいは親の`namespaces`レコードから取得) と現在のカーソルの名前を結合して、現在の名前空間のFQNを生成する。
    * ルートとなるグローバル名前空間のFQNは `"(global)"` とする。
* **4.4. 各要素定義時の現在の名前空間の特定:**
    * 関数、変数などの定義カーソルを処理する際、スコープスタックのトップにある `namespace_id` を取得し、データベースに記録する際に使用する。

---

この計画書に基づき、次のステップとしてデータベーススキーマの具体的な変更定義 (`CREATE TABLE`, `ALTER TABLE` 文) や、パーサー内の詳細な処理フローの設計に進むことができます。

---

> me  

ありがとうございます．次は`Step2: データベーススキーマの設計に関して，実装する手前くらいの具体的な内容の計画書をステップバイステップで書いてください．

---

> gemini 2.5 pro preview

はい、承知いたしました。「Step 2: データベーススキーマの設計」について、実装手前までの具体的な計画書をステップバイステップで記述します。

---

## データベーススキーマ設計計画書 (Step 2 詳細)

### 1. 目的と対象データベース

**1.1. 目的**
C++の名前空間情報を永続化し、プログラム要素との関連を記録するためのデータベーススキーマを定義する。これにより、前回「Step 1」で定義した名前空間の属性と要素の所属情報を格納可能にする。

**1.2. 対象データベースとスクリプト**
このスキーマ変更は、以下の両方のデータベースに適用される。

* ヘッダーファイル情報用データベース (デフォルト: `definitions.db`)
    * 対応スクリプト: `c_cxx_source_parser/header_parser.py` 内の `setup_database` 関数
* 実装ファイル情報用データベース (デフォルト: `implementations.db`)
    * 対応スクリプト: `c_cxx_source_parser/impl_parser.py` 内の `setup_database` 関数

両方の `setup_database` 関数に同様のスキーマ変更を実装する必要がある。

### 2. `namespaces` テーブルの新規作成

名前空間エンティティ自体の情報を格納するための新しいテーブル `namespaces` を作成する。

**2.1. テーブル定義 (SQLite SQL)**

```sql
CREATE TABLE IF NOT EXISTS namespaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, -- 例: "MyLib", "(anonymous)", "(global)"
    parent_namespace_id INTEGER, -- 自己参照。トップレベル/グローバルはNULL
    file_id INTEGER NOT NULL, -- この名前空間定義が最初に見つかったfiles.id
    location TEXT NOT NULL, -- "filename:line:column" または グローバルなら "N/A"
    full_qualified_name TEXT NOT NULL UNIQUE, -- 例: "(global)::MyLib", "(global)::MyLib::(anonymous)_XYZ"
    FOREIGN KEY (parent_namespace_id) REFERENCES namespaces (id) ON DELETE CASCADE,
    FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
);
```

**2.2. 各カラムの詳細説明**

* **`id`**: INTEGER PRIMARY KEY AUTOINCREMENT
    * 役割: `namespaces` テーブル内の各レコードに対する一意な識別子。
* **`name`**: TEXT NOT NULL
    * 役割: 名前空間の名前。
    * 詳細:
        * 通常名前空間: `"MyLib"`, `"Utils"` など。
        * 匿名名前空間: パーサーが生成する一意な名前（例: `"(anonymous)_hashPart"`）や、FQNで区別されるため単に `"(anonymous)"` でも可。Step1のFQN生成ルールと整合させる。
        * グローバル名前空間: `"(global)"`。
* **`parent_namespace_id`**: INTEGER
    * 役割: この名前空間がネストしている場合の、直接の親となる名前空間の `id`。
    * 制約: `FOREIGN KEY (parent_namespace_id) REFERENCES namespaces (id) ON DELETE CASCADE`。親が削除されたら子も削除。
    * 詳細: グローバル名前空間自身、またはグローバル直下の名前空間の親は `NULL`。
* **`file_id`**: INTEGER NOT NULL
    * 役割: この名前空間定義（またはこのFQNを持つ名前空間）が**最初**にソースコード中で検出された `files` テーブルの `id`。
    * 制約: `FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE`。
    * 詳細: グローバル名前空間の場合、特定のファイルに紐づかないため、特別な扱い（例: 挿入時にパーサーが現在処理中の `file_id` を使うか、あるいは `files` テーブルに `(system)` のような特殊レコードを用意しそのIDを使う）を検討する。最もシンプルなのは、`(global)` レコードの `file_id` はパーサー起動時に決まる特定の `file_id`（例えば、最初に解析を指示されたファイル）に関連づけるか、あるいは `setup_database` 時に `files` テーブルに `filepath = '(global_namespace_anchor)'` のようなダミーレコードを挿入し、そのIDを使用する。 **今回は、`(global)` レコードの `file_id` は、`setup_database`時に `files`テーブルに専用のレコード（例：`filepath = '(global_context)'`）を挿入し、そのIDを使用する方針とします。**
* **`location`**: TEXT NOT NULL
    * 役割: この名前空間定義がソースコード中で開始される位置。
    * フォーマット: `"filename:line:column"`
    * 詳細: グローバル名前空間の場合は `"N/A"` や `file_id` に関連するファイルパスのみを記録。
* **`full_qualified_name`**: TEXT NOT NULL UNIQUE
    * 役割: グローバルスコープからのパスを含む、システム全体で一意となる名前空間名。
    * フォーマット例: `"(global)::MyNamespace::MyNestedNamespace"`, `"(global)::(anonymous)_fileXYZ_line10"`
    * 制約: `UNIQUE`。これにより、異なる場所で定義されても同じ論理的な名前空間は単一のレコードで（IDで）管理できる。

**2.3. インデックス定義 (SQLite SQL)**
検索パフォーマンスを向上させるために、以下のインデックスを作成する。

```sql
CREATE INDEX IF NOT EXISTS idx_namespaces_name ON namespaces (name);
CREATE INDEX IF NOT EXISTS idx_namespaces_parent_id ON namespaces (parent_namespace_id);
CREATE INDEX IF NOT EXISTS idx_namespaces_file_id ON namespaces (file_id);
-- full_qualified_name は UNIQUE 制約により自動的にインデックスが作成されることが多いが、明示しても良い。
CREATE UNIQUE INDEX IF NOT EXISTS idx_namespaces_fqn ON namespaces (full_qualified_name);
```

**2.4. グローバルスコープ用レコードの初期データ挿入**
`setup_database` 関数のテーブル作成後、グローバルスコープを表すレコードを `namespaces` テーブルに挿入する。

1.  `files` テーブルにグローバルコンテキスト用のダミーファイルレコードを挿入 (存在しない場合のみ)。
    ```python
    # In setup_database(conn) function
    cursor = conn.cursor()
    global_context_filepath = '(global_context)'
    cursor.execute("SELECT id FROM files WHERE filepath = ?", (global_context_filepath,))
    global_file_record = cursor.fetchone()
    if not global_file_record:
        cursor.execute("INSERT INTO files (filepath, last_parsed_at) VALUES (?, CURRENT_TIMESTAMP)", (global_context_filepath,))
        global_file_id = cursor.lastrowid
    else:
        global_file_id = global_file_record[0]
    ```

2.  `namespaces` テーブルにグローバル名前空間レコードを挿入 (存在しない場合のみ)。
    ```python
    # (続き)
    global_namespace_name = "(global)"
    global_namespace_fqn = "(global)"
    # INSERT OR IGNORE を使用して、UNIQUE 制約違反の場合でもエラーにせずスキップする
    cursor.execute("""
        INSERT OR IGNORE INTO namespaces (name, parent_namespace_id, file_id, location, full_qualified_name)
        VALUES (?, NULL, ?, ?, ?)
    """, (global_namespace_name, global_file_id, "N/A", global_namespace_fqn))
    conn.commit() # 忘れずにコミット
    ```
    このレコードの `id` (通常は1になるが、保証はされないためパーサーはFQNで検索してIDを取得する) が、グローバルスコープに属する要素の `namespace_id` となる。

### 3. 既存テーブルへの `namespace_id` カラム追加

以下の既存テーブルに、要素が属する名前空間を指し示す `namespace_id` カラムを追加する。

**3.1. 対象テーブル一覧**
* `functions`
* `variables`
* `structs_unions`
* `enums`
* `typedefs`

**3.2. 追加する `namespace_id` カラムの仕様**
* カラム名: `namespace_id`
* データ型: `INTEGER`
* 制約: `NOT NULL` (全ての要素は何らかの名前空間に属するため)。`FOREIGN KEY (namespace_id) REFERENCES namespaces (id) ON DELETE CASCADE`。
    * `ON DELETE CASCADE`: 参照先の名前空間レコードが削除された場合、関連する要素も自動的に削除される。これは既存のスキーマの `file_id` の外部キー制約と一貫性がある。

**3.3. 各テーブルへのカラム追加SQL (SQLite SQL)**
`setup_database` 関数内で、各テーブルに対して以下の形式の `ALTER TABLE` 文を実行する。既存のテーブル作成後に一度だけ実行されるようにするか、あるいはテーブルが存在しない場合に `CREATE TABLE` 文を修正して `namespace_id` カラムを最初から含める。ここでは後者を推奨（新規セットアップがクリーンになるため）。

もし `ALTER TABLE` を使う場合の例:
```sql
-- functions テーブルへの追加例
ALTER TABLE functions ADD COLUMN namespace_id INTEGER REFERENCES namespaces(id) ON DELETE CASCADE;
-- (必要であれば NOT NULL 制約も追加するが、既存データがある場合は注意が必要。
--  新規スキーマなら CREATE TABLE 時に NOT NULL を指定)

-- CREATE TABLE 文を修正する場合の functions テーブルの例:
CREATE TABLE IF NOT EXISTS functions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    namespace_id INTEGER NOT NULL, -- 追加
    name TEXT NOT NULL,
    return_type TEXT,
    parameters TEXT,
    is_declaration INTEGER NOT NULL, -- (impl_parser のみ: 0: 定義, 1: 宣言) header_parserでは DEFAULT 1
    -- (impl_parser のみ) is_static INTEGER DEFAULT 0,
    -- (impl_parser のみ) parent_kind TEXT,
    -- (impl_parser のみ) parent_name TEXT,
    location TEXT,
    FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE,
    FOREIGN KEY (namespace_id) REFERENCES namespaces (id) ON DELETE CASCADE -- 追加
);
```
全ての対象テーブル (`variables`, `structs_unions`, `enums`, `typedefs`) の `CREATE TABLE` 文を同様に修正し、`namespace_id` カラムと外部キー制約を追加する。

### 4. 考慮事項

* **既存データベースのマイグレーション:** この計画は、データベースを新規に作成する場合、または既存のデータベースを一度削除して再作成する場合を想定している。既存データを保持したままスキーマを更新する場合は、より複雑なマイグレーション手順（デフォルト値の設定、既存レコードへの `namespace_id` のバックフィルなど）が必要になるが、今回の開発フェーズではスコープ外とする。
* **スキーマバージョニング:** 将来的なスキーマ変更管理のためにはスキーマバージョニングの仕組み（例: `PRAGMA user_version` や専用のバージョン管理テーブル）を導入することが望ましいが、今回はスコープ外とする。

### 5. データベースドキュメントの更新

以下のデータベーススキーマドキュメントファイルを更新し、今回の変更を反映させる。

* `manuals/c_cxx_header_definition_database_schema_documentation.md`
* `manuals/c_cxx_implementation_file_database_schema_documentation.md`

**更新内容の概要:**
* 新しい `namespaces` テーブルのスキーマ定義（カラム、型、制約、インデックス）とその説明。
* 既存テーブル (`functions`, `variables`, `structs_unions`, `enums`, `typedefs`) のスキーマ定義に `namespace_id` カラムが追加されたこと、およびその説明（`namespaces` テーブルへの外部キーであることなど）。
* テーブル間のリレーションシップ図を更新し、`namespaces` テーブルとの関連を含める。

---

この計画書に基づき、`header_parser.py` および `impl_parser.py` の `setup_database` 関数を修正し、新しいスキーマを構築するSQL文を実装することができます。

---

> me  

ありがとうございました．おそらくその方針で将来のプロジェクト目標にも適うと考えられます．一応その目標について書くと，現在のファイル単位で管理している内容を，namespaceや他のようそを利用して論理的な切り分けを経て，ファイルに依存しないような内容に抽象化するためにnamespaceのテーブルが必要です．つまり，先ず現行のファイルに依存するデータベースを作り，次の段階として上記のような２つめのデータベースに適切に再構築するという方法を考えています．

ではStep3についても実装手前くらいの具体的な内容の計画書をステップバイステップで書いてください．

---

> gemini 2.5 pro preview

はい、承知いたしました。将来のプロジェクト目標（ファイル非依存の論理的データベース構築）を拝見し、その中間ステップとして名前空間情報をファイル依存DBに正確に格納することが重要であると理解しました。

それでは、「Step 3: パーサーのスコープ追跡機能」について、実装手前までの具体的な内容の計画書をステップバイステップで記述します。

-----

## パーサーのスコープ追跡機能 実装計画書 (Step 3 詳細)

### 1\. 目的と対象範囲

**1.1. 目的**
`header_parser.py` および `impl_parser.py` のパーサーに、C++の名前空間スコープを正確に追跡する機能を実装する。これにより、各プログラム要素（関数、変数、型など）がどの名前空間に属しているかを特定し、その情報をデータベースの `namespace_id` として記録できるようにする。

**1.2. 対象スクリプトと主要変更関数**

  * 対象スクリプト:
      * `c_cxx_source_parser/header_parser.py`
      * `c_cxx_source_parser/impl_parser.py`
  * 主要変更関数: 両スクリプト内の `traverse_ast` 関数。
  * 新規追加: スコープ管理やFQN生成、データベースアクセスを行うためのヘルパー関数群。

### 2\. スコープ追跡メカニズム

**2.1. スコープスタックの設計**
`traverse_ast` 関数内で名前空間のネスト構造を管理するためにスタックを使用する。

  * **スタック要素のデータ構造:**
    各要素は、現在の名前空間スコープを表すタプル `(namespace_db_id: int, current_fqn: str)` とする。

      * `namespace_db_id`: `namespaces` テーブルに記録された、この名前空間の `id`。
      * `current_fqn`: この名前空間の完全修飾名 (FQN)。

  * **初期状態:**
    `traverse_ast` の呼び出し時に、スコープスタックはグローバル名前空間の情報で初期化される。グローバル名前空間の `id` と FQN (`"(global)"`) を `setup_database` 後に取得（または固定値として定義）し、スタックの最初の要素としてプッシュする。

    ```python
    # traverse_ast の冒頭（または呼び出し側）
    global_ns_id = get_global_namespace_id(db_conn) # ヘルパー関数で (global) の ID を取得
    global_fqn = "(global)"
    scope_stack = [(global_ns_id, global_fqn)]
    ```

  * **プッシュ/ポップ操作:**

      * `CursorKind.NAMESPACE` に入る際: 新たに処理する名前空間の `(namespace_db_id, current_fqn)` をスタックにプッシュ。
      * `CursorKind.NAMESPACE` から出る際: スタックからポップ。

**2.2. `traverse_ast` 関数のシグネチャ変更**
スコープスタックを再帰呼び出し間で共有するため、`traverse_ast` 関数の引数に追加する。

旧シグネチャ (例):
`def traverse_ast(cursor, db_conn, file_id, target_filepath):`

新シグネチャ (例):
`def traverse_ast(cursor, db_conn, file_id, target_filepath, scope_stack):`

### 3\. `traverse_ast` 関数内の処理フロー

**3.1. `CursorKind.NAMESPACE` 検出時の処理**
ASTノードが名前空間定義 (`clang.cindex.CursorKind.NAMESPACE`) である場合の処理フロー。

  * **3.1.1. 名前空間属性の抽出:**

      * `name_str = cursor.spelling` (空文字列の場合は匿名を示唆)
      * `is_anonymous_ns = cursor.is_anonymous()` (Clangバインディングにこの直接的なメソッドがない場合、`name_str == ""` で代用)
      * `location_str = f"{os.path.basename(cursor.location.file.name)}:{cursor.location.line}:{cursor.location.column}" if cursor.location and cursor.location.file else "N/A"`
      * `current_file_id = file_id` (現在の処理対象ファイルのID)

  * **3.1.2. 親名前空間情報の取得:**
    スコープスタックのトップ（現在の親スコープ）から情報を取得。

    ```python
    parent_ns_db_id, parent_fqn = scope_stack[-1]
    ```

  * **3.1.3. 完全修飾名 (FQN) の生成:**
    ヘルパー関数 `generate_fqn(parent_fqn, name_str, is_anonymous_ns, current_file_id, cursor.location)` を呼び出してFQNを生成 (詳細は 4.1)。

  * **3.1.4. `namespaces` テーブルへの登録とID取得:**
    ヘルパー関数 `get_or_create_namespace_db_entry(db_conn, generated_fqn, name_str_for_db, parent_ns_db_id, current_file_id, location_str)` を呼び出して、`namespaces` テーブルへの登録または既存エントリのID取得を行う (詳細は 4.2)。`name_str_for_db` は、匿名の場合 `"(anonymous)"` など、DB格納用の名前。

  * **3.1.5. スコープスタックへのプッシュ:**
    取得した `namespace_db_id` と生成した `generated_fqn` をタプルとしてスタックにプッシュ。

    ```python
    scope_stack.append((new_namespace_db_id, generated_fqn))
    ```

  * **3.1.6. 子ノードの再帰呼び出し:**
    現在の `scope_stack` を渡して、名前空間内の子ノードを再帰的に処理。

    ```python
    for child in cursor.get_children():
        traverse_ast(child, db_conn, file_id, target_filepath, scope_stack)
    ```

  * **3.1.7. スコープスタックからのポップ:**
    当該名前空間の処理が完了したら、スタックをポップして親スコープに戻る。

    ```python
    scope_stack.pop()
    ```

**3.2. その他の定義要素 (`FUNCTION_DECL`, `VAR_DECL` 等) 検出時の処理**
関数、変数、クラスなどの定義を検出した場合。

  * **3.2.1. 現在の `namespace_id` をスタックから取得:**

    ```python
    current_namespace_db_id, _ = scope_stack[-1]
    ```

  * **3.2.2. データベース挿入処理への `namespace_id` の追加:**
    既存のデータベース挿入ロジック (`db_cursor.execute("INSERT INTO functions ...")` など) を修正し、取得した `current_namespace_db_id` を `namespace_id` カラムの値として含める。

    例 (`functions` テーブルへの挿入):

    ```python
    # 既存の引数に加え current_namespace_db_id を渡す
    db_cursor.execute(
        "INSERT INTO functions (file_id, namespace_id, name, ..., location) VALUES (?, ?, ?, ..., ?)",
        (file_id, current_namespace_db_id, name, ..., location)
    )
    ```

    これは、`header_parser.py` と `impl_parser.py` の両方で、対象となるすべての定義種別に対して同様の修正が必要。

### 4\. ヘルパー関数の設計

**4.1. `generate_fqn(parent_fqn, current_name_str, is_anonymous, file_path_for_uniqueness, location_for_uniqueness)`** (仮称)

  * **目的:** 親のFQNと現在の名前空間の情報から、新しいFQNを生成する。
  * **引数:**
      * `parent_fqn`: 親名前空間のFQN (文字列)。
      * `current_name_str`: 現在の名前空間名 (`cursor.spelling`)。
      * `is_anonymous`: 匿名名前空間かどうかのフラグ (ブール値)。
      * `file_path_for_uniqueness`: 匿名名前空間のFQNを一意にするためのファイルパス (文字列)。
      * `location_for_uniqueness`: 匿名名前空間のFQNを一意にするための位置情報 (Clang Locationオブジェクトなど、行・列を取得できるもの)。
  * **戻り値:** 生成されたFQN (文字列)。
  * **ロジック概要:**
      * `current_name_str` が空または `is_anonymous` が True の場合:
          * 匿名名前空間用の名前を生成。例: `"(anonymous)"`。
          * 一意性を高めるため、ファイルパスや位置情報からハッシュや簡略表現をサフィックスとして追加する。例: `f"{parent_fqn}::(anonymous)_{os.path.basename(file_path_for_uniqueness)}_{location_for_uniqueness.line}_{location_for_uniqueness.column}"`。このサフィックス戦略は、ファイル内でユニークであれば十分。
      * `current_name_str` が存在する場合:
          * `f"{parent_fqn}::{current_name_str}"` を返す。
      * 親が `"(global)"` の場合は、`"(global)::"` をプレフィックスとする。

**4.2. `get_or_create_namespace_db_entry(db_conn, fqn, name_for_db, parent_db_id, file_id, location_str)`** (仮称)

  * **目的:** 指定されたFQNを持つ名前空間がDBに存在するか確認し、存在すればそのIDを返す。存在しなければ新規登録して新しいIDを返す。
  * **引数:**
      * `db_conn`: SQLiteコネクションオブジェクト。
      * `fqn`: 登録/検索対象のFQN。
      * `name_for_db`: DBの `name` カラムに格納する名前（匿名なら `"(anonymous)"` など）。
      * `parent_db_id`: 親名前空間のDB上のID。
      * `file_id`: このFQNが最初に見つかった `files.id`。
      * `location_str`: このFQNが最初に見つかった位置。
  * **戻り値:** `namespaces` テーブルの `id` (整数)。
  * **ロジック概要:**
    1.  `db_cursor = db_conn.cursor()`
    2.  `db_cursor.execute("SELECT id FROM namespaces WHERE full_qualified_name = ?", (fqn,))`
    3.  `row = db_cursor.fetchone()`
    4.  `if row:`
          * `return row[0]` (既存エントリのID)
    5.  `else:`
          * `db_cursor.execute("INSERT INTO namespaces (name, parent_namespace_id, file_id, location, full_qualified_name) VALUES (?, ?, ?, ?, ?)", (name_for_db, parent_db_id, file_id, location_str, fqn))`
          * `db_conn.commit()` (即時コミットするか、最後にまとめてコミットするかは全体のトランザクション戦略による。現状のパーサーは最後にまとめてコミットしているので、それに合わせるならここではコミットしない)
          * `return db_cursor.lastrowid` (新規エントリのID)

**4.3. `get_global_namespace_id(db_conn)`** (仮称)

  * **目的:** グローバル名前空間 `"(global)"` のDB上のIDを取得する。
  * **ロジック概要:** `SELECT id FROM namespaces WHERE full_qualified_name = '(global)'` を実行してIDを返す。`setup_database` でこのレコードが確実に作成されている前提。

### 5\. `header_parser.py` と `impl_parser.py` への共通適用と差異

  * **共通ロジック:** 上記のスコープスタック管理、FQN生成、DBアクセスヘルパー関数呼び出しのロジックは、両パーサーでほぼ共通となる。これらのヘルパー関数は、共通のユーティリティモジュールに配置することも検討できるが、まずは各ファイル内にプライベート関数として実装しても良い。
  * **差異の考慮:**
      * **`impl_parser.py` のクラスメンバー:** `impl_parser.py` はクラスのメンバー関数 (`parent_kind = 'CLASS_DECL'`, `parent_name`) を特別扱いしている。クラス定義自体も何らかの名前空間に属するため、まずクラスが属する `namespace_id` が決まる。そのクラスのメンバー関数は、`functions` テーブルに `namespace_id` (クラスが属する名前空間のID) と `parent_name` (クラス名) の両方を持つことになる。`traverse_ast` 内の `FUNCTION_DECL` の処理で、`semantic_parent` がクラスである場合と名前空間である場合で、`parent_name` と `namespace_id` の設定方法を適切に制御する必要がある。
        具体的には、`semantic_parent` が `TRANSLATION_UNIT` (ファイルスコープ) または `NAMESPACE` なら、`namespace_id` はスタックから、`parent_name` は NULL。`semantic_parent` が `CLASS_DECL` や `STRUCT_DECL` なら、`parent_name` はそのクラス名、`namespace_id` はそのクラス定義が属する名前空間のID (クラス定義を処理する際にスタックから取得して記録しておく必要がある) となる。

### 6\. コード変更箇所の具体的なイメージ (コメント形式)

**`traverse_ast` 関数の変更箇所 (抜粋)**

```python
# def traverse_ast(cursor, db_conn, file_id, target_filepath): # 旧
def traverse_ast(cursor, db_conn, file_id, target_filepath, scope_stack): # 新

    # ... (既存のファイルチェックなど)

    # 現在のスコープIDとFQNを取得
    # current_ns_id, current_parent_fqn = scope_stack[-1] # これは親の情報を指すので注意

    if cursor.kind == CursorKind.NAMESPACE:
        # 3.1.1. 名前空間属性の抽出
        name_str = cursor.spelling
        is_anonymous_ns = (name_str == "") # または cursor.is_anonymous()
        location_str = format_location(cursor.location) # format_locationは既存のものを想定
        current_file_id = file_id

        # 3.1.2. 親名前空間情報の取得
        parent_ns_db_id_for_child, parent_fqn_for_child = scope_stack[-1]

        # 3.1.3. FQNの生成
        # (generate_fqn はファイルパスや位置情報も引数に取るかもしれない)
        generated_fqn = generate_fqn(parent_fqn_for_child, name_str, is_anonymous_ns, target_filepath, cursor.location)
        
        # DB格納用の名前を決定
        db_name = "(anonymous)" if is_anonymous_ns else name_str

        # 3.1.4. namespaces テーブルへの登録とID取得
        new_namespace_db_id = get_or_create_namespace_db_entry(
            db_conn, generated_fqn, db_name, parent_ns_db_id_for_child,
            current_file_id, location_str
        )

        # 3.1.5. スコープスタックへのプッシュ
        scope_stack.append((new_namespace_db_id, generated_fqn))

        # 3.1.6. 子ノードの再帰呼び出し
        for child in cursor.get_children():
            traverse_ast(child, db_conn, file_id, target_filepath, scope_stack) # scope_stack を渡す

        # 3.1.7. スコープスタックからのポップ
        scope_stack.pop()
        return # 名前空間ノード自体の処理はここまで

    # --- その他の定義要素の処理 ---
    # (例: FUNCTION_DECL)
    elif cursor.kind == CursorKind.FUNCTION_DECL:
        # 3.2.1. 現在の namespace_id をスタックから取得
        current_ns_id_for_element, _ = scope_stack[-1]

        # ... (既存の関数情報抽出ロジック: name, return_type, params, is_declaration, etc.)

        # 3.2.2. DB挿入処理への namespace_id の追加
        # db_cursor.execute("INSERT INTO functions (file_id, name, ..., location, namespace_id) ...",
        #                   (file_id, name, ..., location, current_ns_id_for_element))
        # impl_parser.py の場合は、クラスメンバーかどうかの判定もここで行い、
        # parent_name と namespace_id を適切に設定する。
        # semantic_parent = cursor.semantic_parent
        # if semantic_parent and semantic_parent.kind in [CursorKind.CLASS_DECL, CursorKind.STRUCT_DECL]:
        #     # クラスメンバーの場合。このクラスが属する namespace_id が必要。
        #     # クラス定義を処理する際に、そのクラスの namespace_id を記録しておく必要がある。
        #     # この namespace_id はスコープスタックのトップではなく、クラス自身の属性として持つ。
        #     class_parent_name = semantic_parent.spelling
        #     # class_namespace_id = get_namespace_id_for_class(semantic_parent) #別途機構が必要
        #     # db_cursor.execute(..., parent_name=class_parent_name, namespace_id=class_namespace_id)
        # else:
        #     # 通常の名前空間直下の関数
        #     # db_cursor.execute(..., parent_name=None, namespace_id=current_ns_id_for_element)


    # ... (他の定義種類 VAR_DECL, STRUCT_DECL なども同様に namespace_id を追加)

    # --- 再帰呼び出し (NAMESPACE 以外のノードの子を辿る場合) ---
    # (注意: 既存のロジックでは、NAMESPACE以外のノードでも get_children() を呼んでいる場合がある。
    #  その場合も scope_stack を適切に引き渡す必要がある。)
    # if cursor.kind != CursorKind.NAMESPACE: # 上でNAMESPACEはreturnしているので不要な条件分岐かも
    #    for child in cursor.get_children():
    #        traverse_ast(child, db_conn, file_id, target_filepath, scope_stack)

```

**`main` 関数の変更箇所 (抜粋)**

```python
def main():
    # ... (引数パース、Clangセットアップなど)
    try:
        # ... (tu = index.parse(...) )
        conn = setup_database(db_filepath)
        target_filepath_abs = os.path.abspath(source_filepath)
        file_id_for_this_run = add_file_record(conn, source_filepath) # 既存

        # グローバルスコープ情報の準備
        global_ns_id = get_global_namespace_id(conn) # ヘルパー関数 (DBからSELECT)
        initial_scope_stack = [(global_ns_id, "(global)")]

        traverse_ast(tu.cursor, conn, file_id_for_this_run, target_filepath_abs, initial_scope_stack) # scope_stack を渡す

        conn.commit()
        conn.close()
    # ... (エラーハンドリング)
```

-----

この計画書に基づき、必要なヘルパー関数を定義し、`traverse_ast` および `main` 関数を修正することで、名前空間のスコープ追跡機能が実装できるはずです。特に `impl_parser.py` におけるクラスメンバーと名前空間の二重のスコープ管理には注意が必要です。クラス定義自体も名前空間に属するため、クラスを処理する際にその `namespace_id` を決定し、メンバー関数はそのクラスの `parent_name` と、クラスが属する `namespace_id` を持つことになります。

---