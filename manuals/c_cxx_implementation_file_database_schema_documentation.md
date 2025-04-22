---
title: "C/C++ Implementation File Database Schema Documentation"
author: "Masaya-Sakamoto"
date: "2025-04-22"
lang: en
---

# C/C++ Implementation File Database Schema Documentation

## Index
1. [Overview](#1-overview)
2. [Basic Concepts](#2-basic-concepts)
3. [Table Definitions](#3-table-definitions)
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

This database is designed by the `impl_parser.py` script to store information extracted from C/C++ **implementation files** (e.g., `.c`, `.cpp`). While it can capture various definitions similar to the header parser, its primary focus is on storing details about **function definitions** (including scope and static linkage) and **global/static variable definitions** (including linkage and the presence of initializers).

This database complements the one generated from header files, providing insights into where declarations are actually defined and implemented. It uses SQLite and is stored as a single file (e.g., `implementations.db`).

## 2. Basic Concepts

* **File-Centric Structure:** All entries are linked to the specific implementation file they were found in via the `files` table.
* **`file_id`:** A foreign key in each definition table referencing `files.id`, linking the definition to its source file.
* **`ON DELETE CASCADE`:** Ensures that deleting a file record from the `files` table automatically removes all associated definition records from other tables.
* **`location` Column:** Stores the location (`filename:line:column`) within the source file where the definition or declaration occurs.
* **Scope Focus:** The parser primarily targets definitions and declarations found at **file scope** (global or `static`) or directly within C++ **class/struct/namespace scopes**. Definitions local to function bodies are generally excluded from storage.
* **TEXT Type Usage:** Complex list-like data (parameters, members, enum constants) are stored as formatted `TEXT` for simplicity.

## 3. Table Definitions

Details of each table and its columns are provided below. Note the extensions in the `functions` and `variables` tables compared to the header-focused schema.

### 3.1. `files` Table

Manages the implementation files that have been parsed. (Identical to the header schema)

| Column Name      | Data Type    | Constraints                       | Description                                                                   |
| :--------------- | :----------- | :-------------------------------- | :---------------------------------------------------------------------------- |
| `id`             | `INTEGER`    | `PRIMARY KEY`, `AUTOINCREMENT`    | Unique identifier for the file record.                                        |
| `filepath`       | `TEXT`       | `UNIQUE`, `NOT NULL`              | The absolute path to the parsed source file. Prevents duplicate registration. |
| `last_parsed_at` | `TIMESTAMP`  | `DEFAULT CURRENT_TIMESTAMP`       | The timestamp when this file was last parsed and its data updated/inserted.   |

### 3.2. `macros` Table

Stores information about macros defined using `#define` *within* the implementation file. (Identical schema to the header version)

| Column Name | Data Type | Constraints                                        | Description                                                                                 |
| :---------- | :-------- | :------------------------------------------------- | :------------------------------------------------------------------------------------------ |
| `id`        | `INTEGER` | `PRIMARY KEY`, `AUTOINCREMENT`                     | Unique identifier for the macro definition.                                                 |
| `file_id`   | `INTEGER` | `NOT NULL`, `FOREIGN KEY (files.id) ON DELETE CASCADE` | Reference to the file (`files` table) where this macro is defined.                        |
| `name`      | `TEXT`    | `NOT NULL`                                         | The name of the macro.                                                                      |
| `body`      | `TEXT`    |                                                    | The body of the macro (content it expands to). Can be `NULL` if no body.                    |
| `location`  | `TEXT`    |                                                    | Location where the macro is defined (`filename:line:column`).                             |

**Index:** `idx_macro_name` (on `name` column): Speeds up searches by macro name.

### 3.3. `functions` Table

Stores function **definitions** (primarily) and declarations found in implementation files. (Extended schema)

| Column Name      | Data Type | Constraints                                        | Description                                                                                     |
| :--------------- | :-------- | :------------------------------------------------- | :---------------------------------------------------------------------------------------------- |
| `id`             | `INTEGER` | `PRIMARY KEY`, `AUTOINCREMENT`                     | Unique identifier for the function entry.                                                       |
| `file_id`        | `INTEGER` | `NOT NULL`, `FOREIGN KEY (files.id) ON DELETE CASCADE` | Reference to the file where this function is defined/declared.                                |
| `name`           | `TEXT`    | `NOT NULL`                                         | The name of the function.                                                                     |
| `return_type`    | `TEXT`    |                                                    | The return type of the function. May be special values like `(constructor)` for C++.          |
| `parameters`     | `TEXT`    |                                                    | Parameter list (types and names). Stored as simple text.                                        |
| `is_declaration` | `INTEGER` | `NOT NULL`                                         | **0 if it's a definition (has a body), 1 if it's a declaration (prototype).** |
| `is_static`      | `INTEGER` | `DEFAULT 0`                                        | **1 if the function is declared with `static` (file scope linkage), 0 otherwise.** |
| `parent_kind`    | `TEXT`    |                                                    | **For C++: Kind of the parent scope (e.g., 'CLASS_DECL', 'NAMESPACE'). NULL otherwise.** |
| `parent_name`    | `TEXT`    |                                                    | **For C++: Name of the parent class, struct, or namespace. NULL otherwise.** |
| `location`       | `TEXT`    |                                                    | Location where the function is defined/declared (`filename:line:column`).                     |

**Indices:**
* `idx_func_name` (on `name` column): Speeds up searches by function name.
* `idx_func_parent` (on `parent_name` column): Speeds up searches for methods of a specific class/namespace.

### 3.4. `structs_unions` Table

Stores definitions of structures (`struct`) and unions (`union`) found at file scope within implementation files. (Identical schema to the header version)

| Column Name | Data Type | Constraints                                        | Description                                                                                         |
| :---------- | :-------- | :------------------------------------------------- | :-------------------------------------------------------------------------------------------------- |
| `id`        | `INTEGER` | `PRIMARY KEY`, `AUTOINCREMENT`                     | Unique identifier for the struct/union definition.                                                |
| `file_id`   | `INTEGER` | `NOT NULL`, `FOREIGN KEY (files.id) ON DELETE CASCADE` | Reference to the file containing this definition.                                                   |
| `kind`      | `TEXT`    | `NOT NULL`                                         | Indicates whether it is a `'struct'` or a `'union'`.                                                |
| `name`      | `TEXT`    |                                                    | The name (tag) of the struct/union. Can be `NULL` for anonymous types.                            |
| `members`   | `TEXT`    |                                                    | Textual representation of the member list.                                                          |
| `location`  | `TEXT`    |                                                    | Location where the struct/union is defined (`filename:line:column`).                              |

**Index:** `idx_struct_name` (on `name` column): Speeds up searches by struct/union name.

### 3.5. `enums` Table

Stores definitions of enumerations (`enum`) found at file scope within implementation files. (Identical schema to the header version)

| Column Name | Data Type | Constraints                                        | Description                                                                                            |
| :---------- | :-------- | :------------------------------------------------- | :----------------------------------------------------------------------------------------------------- |
| `id`        | `INTEGER` | `PRIMARY KEY`, `AUTOINCREMENT`                     | Unique identifier for the enum definition.                                                             |
| `file_id`   | `INTEGER` | `NOT NULL`, `FOREIGN KEY (files.id) ON DELETE CASCADE` | Reference to the file containing this definition.                                                      |
| `name`      | `TEXT`    |                                                    | The name (tag) of the enum. Can be `NULL` for anonymous enums.                                       |
| `constants` | `TEXT`    |                                                    | Comma-separated list of enum constants, potentially including values. Stored as simple text.          |
| `location`  | `TEXT`    |                                                    | Location where the enum is defined (`filename:line:column`).                                         |

**Index:** `idx_enum_name` (on `name` column): Speeds up searches by enum name.

### 3.6. `typedefs` Table

Stores type definitions using `typedef` found at file scope within implementation files. (Identical schema to the header version)

| Column Name       | Data Type | Constraints                                        | Description                                                                                       |
| :---------------- | :-------- | :------------------------------------------------- | :------------------------------------------------------------------------------------------------ |
| `id`              | `INTEGER` | `PRIMARY KEY`, `AUTOINCREMENT`                     | Unique identifier for the typedef definition.                                                     |
| `file_id`         | `INTEGER` | `NOT NULL`, `FOREIGN KEY (files.id) ON DELETE CASCADE` | Reference to the file containing this definition.                                                   |
| `name`            | `TEXT`    | `NOT NULL`                                         | The new type name created by the `typedef`.                                                       |
| `underlying_type` | `TEXT`    |                                                    | The original type that `name` is an alias for.                                                    |
| `location`        | `TEXT`    |                                                    | Location where the `typedef` is defined (`filename:line:column`).                               |

**Index:** `idx_typedef_name` (on `name` column): Speeds up searches by typedef name.

### 3.7. `variables` Table

Stores definitions and declarations of global or file-static variables. (Extended schema)

| Column Name       | Data Type | Constraints                                        | Description                                                                                     |
| :---------------- | :-------- | :------------------------------------------------- | :---------------------------------------------------------------------------------------------- |
| `id`              | `INTEGER` | `PRIMARY KEY`, `AUTOINCREMENT`                     | Unique identifier for the variable entry.                                                       |
| `file_id`         | `INTEGER` | `NOT NULL`, `FOREIGN KEY (files.id) ON DELETE CASCADE` | Reference to the file containing this declaration/definition.                                 |
| `name`            | `TEXT`    | `NOT NULL`                                         | The name of the variable.                                                                     |
| `type`            | `TEXT`    |                                                    | The data type of the variable.                                                                  |
| `is_extern`       | `INTEGER` | `DEFAULT 0`                                        | 1 if declared with `extern` (a declaration only), 0 otherwise (likely a definition).            |
| `is_static`       | `INTEGER` | `DEFAULT 0`                                        | **1 if the variable is declared with `static` (file scope linkage), 0 otherwise.** |
| `has_initializer` | `INTEGER` | `DEFAULT 0`                                        | **1 if the variable definition appears to have an initializer, 0 otherwise (based on AST check).** |
| `location`        | `TEXT`    |                                                    | Location where the variable is declared/defined (`filename:line:column`).                     |

**Index:** `idx_var_name` (on `name` column): Speeds up searches by variable name.

## 4. Relationships

As with the header schema, the primary relationship links records in the definition tables (`macros`, `functions`, `variables`, etc.) back to the specific implementation file they were parsed from in the `files` table, using the `file_id`.

## 5. Indices

Indices are created on common search columns (like `name`) in each definition table to optimize query performance. A specific index `idx_func_parent` is added to the `functions` table to efficiently find methods belonging to a particular C++ class or namespace.
