---
title: "C/C++ Header Definition Database Schema Documentation"
author: "Masaya-Sakamoto"
date: "2025-04-21"
lang: en
---

# C/C++ Header Definition Database Schema Documentation

## Index
1. [Overview](#1-overview)
2. [Basic Concepts](#2-basic-concepts)
3. [Table Descriptions](#3-table-descriptions)
    - [3.1. `files` Table](#31-files-table)
    - [3.2. `macros` Table](#32-macros-table)
    - [3.3. `functions` Table](#33-functions-table)
    - [3.4. `structs_unions` Table](#34-structs_unions-table)
    - [3.5. `enums` Table](#35-enums-table)
    - [3.6. `typedefs` Table](#36-typedefs-table)
    - [3.7. `variables` Table](#37-variables-table)
4. [Relationships](#4-relationships)
5. [Indices](#5-indices)

## 1. Overview

This database is designed to store various definitions (macros, functions, structures, unions, enumerations, typedefs, global variables) extracted from C/C++ header files (.h, .hpp, etc.) by the `header_parser.py` script. The purpose is to make the definitions within a codebase searchable, referable, and analyzable.

The database uses SQLite and is stored as a single file (e.g., `definitions.db`).

## 2. Basic Concepts

- **File-Centric Design**: All definitions are associated with the header file in which they are defined. The files table is central, and other definition tables reference this files table.
- **`file_id`**: Each definition table has a `file_id` column, which is a foreign key referencing the `id` in the `files` table. This allows for easy identification of all definitions contained within a specific file.
- **`ON DELETE CASCADE`**: The foreign key constraints specify `ON DELETE CASCADE`. This means that if a file record is deleted from the `files` table, all associated definitions (macros, functions, etc.) for that file will also be automatically deleted from the database.
- **`location`**: **Column**: Many tables include a `location` column. This is a string indicating where the definition is located within the source file, typically stored in the format `filename:line_number:column_number` (e.g., `my_header.h:42:10`). This helps in locating the original definition in the code.
- **Use of `TEXT` Type**: Lists such as function parameters, struct/union members, and enum constants are stored in a single formatted `TEXT` column to keep the database structure simple. If more detailed analysis is required, this text data would need to be parsed on the application side.

## 3. Table Descriptions

### 3.1. `files` Table

Manages the header files that have been parsed.

| Column         | Type       | Constraints                    | Description                                         |
| -------------- | ---------- | ------------------------------ | --------------------------------------------------- |
| `id` | INTEGER | PRIMARY KEY, AUTOINCREMENT | Unique identifier for the file record |
| `filepath` | TEXT | UNIQUE, NOT NULL | The absolute path to the parsed header file. Prevents duplicate registration |
| `last_parsed_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | The timestamp when this file was last parsed and its data updated/inserted |

### 3.2. `macros` Table

Stores information about macros defined with `#define`.

| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY, AUTOINCREMENT | Unique identifier for the macro definition |
| `file_id` | INTEGER | NOT NULL, FOREIGN KEY (files.id) ON DELETE CASCADE | Reference to the file (`files` table) where this macro is defined |
| `name` | TEXT | NOT NULL | The name of the macro (e.g., `BUFFER_SIZE`, `MAX_CONNECTIONS`) |
| `body` | TEXT | | The body of the macro (content it expands to). E.g., `1024`, `(x * 2)`. Can be `NULL` if no body (`#define DEBUG`) |
| `location` | TEXT | | Location where the macro is defined (`filename:line:column`) |

**Index:** `idx_macro_name` (on `name` column): Speeds up searches by macro name.

### 3.3. `functions` Table

Stores function declarations (prototypes) and definitions (primarily declarations in header files).

| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY, AUTOINCREMENT | Unique identifier for the function definition/declaration |
| `file_id` | INTEGER | NOT NULL, FOREIGN KEY (files.id) ON DELETE CASCADE | Reference to the file where this function is defined/declared |
| `name` | TEXT | NOT NULL | The name of the function |
| `return_type` | TEXT | | The return type of the function (e.g., `int`, `void*`, `const struct User*`) |
| `parameters`   | TEXT | | Parameter list (including types and names). E.g., "int count, const char* name". Stored as simple text |
| `is_declaration` | INTEGER | DEFAULT 1 | 1 if it's a declaration (prototype), 0 if it's a definition (with body). Usually 1 in headers |
| `location` | TEXT | | Location where the function is defined/declared (filename:line:column) |

**Index:** `idx_func_name`  (on `name` column): Speeds up searches by function name.

### 3.4. `structs_unions` Table

Stores definitions of structures (`struct`) and unions (`union`).

| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY, AUTOINCREMENT | Unique identifier for the struct/union definition |
| `file_id` | INTEGER | NOT NULL, FOREIGN KEY (files.id) ON DELETE CASCADE | Reference to the file containing this definition |
| `kind` | TEXT | NOT NULL |Indicates whether it is a `'struct'` or a `'union'` |
| `name` | TEXT | | The name (tag) of the struct/union. Can be `NULL` for anonymous types (e.g., `typedef struct { ... }`) |
| `members` | TEXT | | Textual representation of the member list. E.g., `"int id; float value;"`. Details of nested structures might not be fully captured |
| `location` | TEXT | | Location where the struct/union is defined (`filename:line:column`) |

**Index:** `idx_struct_name` (on `name` column): Speeds up searches by struct/union name (excluding anonymous types).

### 3.5. `enums` Table

Stores definitions of enumerations (`enum`).

| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY, AUTOINCREMENT | Unique identifier for the enum definition |
| `file_id` | INTEGER | NOT NULL, FOREIGN KEY (files.id) ON DELETE CASCADE | Reference to the file containing this definition |
| `name` | TEXT | | The name (tag) of the enum. Can be NULL for anonymous enums |
| `constants` | TEXT |                                               | Comma-separated list of enum constants, potentially including their values. E.g., `"RED=1, GREEN, BLUE=5"`. Stored as simple text |
| `location` | TEXT | | Location where the enum is defined (`filename:line:column`) |

**Index:** `idx_enum_name` on (on `name` column): Speeds up searches by enum name (excluding anonymous types).

### 3.6. `typedefs` Table

Stores type definitions created with `typedef`.

| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY, AUTOINCREMENT | Unique identifier for the typedef definition |
| `file_id` | INTEGER | NOT NULL, FOREIGN KEY (files.id) ON DELETE CASCADE | Reference to the file containing this definition |
| `name` | TEXT | NOT NULL | The new type name created by the `typedef` |
| `underlying_type` | TEXT | | The original type that name is an alias for. E.g., `int`, `struct Point*`, `void (*)(int)` |
| `location` | TEXT | | Location where the `typedef` is defined (`filename:line:column`) |

**Index:** `idx_typedef_name` (on name column): Speeds up searches by typedef name.

### 3.7. `variables` Table

Stores declarations of global variables or file-scope static variables. Local variables within functions are not included.

| Column | Type | Constraints | Description |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY, AUTOINCREMENT | Unique identifier for the variable declaration |
| `file_id` | INTEGER | NOT NULL, FOREIGN KEY (files.id) ON DELETE CASCADE | Reference to the file containing this declaration |
| `name` | TEXT | NOT NULL | The name of the variable |
| `type` | TEXT | | The data type of the variable |
| `is_extern`| INTEGER | DEFAULT 0 | 1 if the variable is declared with the `extern` keyword, 0 otherwise |
| `location` | TEXT | | Location where the variable is declared (`filename:line:column`) |

**Index:** `idx_var_name` (on `name` column): Speeds up searches by variable name.

## 4. Relationships

The primary relationship exists between the `files` table and all other definition tables (`macros`, `functions`, etc.). Each definition record is linked via its `file_id` column to the specific file record from which it originates. This enables queries such as "What are all the functions in file X?" or "In which file is macro Y defined?".

## 5. Indices

Indices are created on the `name` column (or similar identifier columns) of each definition table. This significantly speeds up searches for definitions based on their names.
