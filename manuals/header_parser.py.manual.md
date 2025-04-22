---
title: "header_parser.py manual"
author: "Masaya-Sakamoto"
date: "2025-04-21"
lang: en
---

# Documentation for `header_parser.py`

## Overview

`header_parser.py` is a tool for parsing C/C++ header files and storing definitions such as functions, structs, and macros into an SQLite database. It utilizes libclang for syntax analysis, making it useful for codebase documentation and preprocessing for static analysis.

## Main Features

- Extracts functions, structs, unions, enums, typedefs, macros, and global variables from C/C++ header files
- Saves analysis results to an SQLite database
- Supports specifying include paths, preprocessor macros, C++ standard versions, and the path to libclang

## Requirements

- Python 3.7 or later
- `clang` Python bindings (requires `libclang`)
- SQLite3 (included in the Python standard library)

## Installation Example

```bash
pip install clang
```

The installation method for `libclang` varies depending on your operating system. Specify the path if necessary.

## Usage

1. Save the above code as `header_parser.py`.
2. Run the script from the terminal.

```bash
# Basic usage: Parse my_header.h and save results to definitions.db
python header_parser.py my_header.h

# Specify the database file name
python header_parser.py my_header.h -db my_definitions.sqlite

# Specify include paths (multiple -I options allowed)
python header_parser.py src/my_header.h -I include -I ../common/include

# Define preprocessor macros (multiple -D options allowed)
python header_parser.py config.h -DMODE=1 -DPLATFORM_LINUX

# Parse as C++ and use the C++14 standard
python header_parser.py my_class.hpp --lang c++ --std c++14

# Explicitly specify the path to libclang (if not found automatically)
python header_parser.py my_header.h --libclang /opt/homebrew/opt/llvm/lib/libclang.dylib
```

## Database Contents

A specified SQLite database file (such as `definitions.db`) will be created, and the analysis results will be stored in the following tables:

- `files`: Information about parsed files.
- `macros`: `#define` macro definitions.
- `functions`: Function declarations (mainly declarations, as these are header files).
- `structs_unions`: Struct and union definitions.
- `enums`: Enum definitions.
- `typedefs`: Type definitions via `typedef`.
- `variables`: Declarations of global and `extern` variables.

Each table includes the name, kind, related information (such as type, parameters, members), the position in the source file (filename:line:column), and a foreign key to the `files` table.

## Notes and Limitations

- **libclang Path**: Depending on your environment, you may need to set the `LIBCLANG_PATH` variable correctly within the script or specify it using the `--libclang` option.
- **Complex Macros**: It can be difficult to fully extract the bodies of function-like or complex macros. The current implementation uses `get_tokens()` to attempt extraction, but there are limitations.
- **Conditional Compilation**: Blocks controlled by `#ifdef`, `#ifndef`, or `#if` depend on the `-D` options passed to Clang. To cover all elements defined under different conditions, you may need to parse the headers multiple times with different `-D` options.
- **C++ Complexity**: Features like templates, namespaces, and overloading in C++ make parsing and database storage more complex. The script extracts basic structures, but may lack information on advanced C++ features.
- **Performance**: Parsing very large header files or files with many includes may take significant time.
- **Error Handling**: If Clang parsing errors occur, diagnostic messages are shown, but processing continues. Stricter error handling may be required for some use cases.
- **Database Schema**: Parameters, members, and enum constants are stored as plain text. A more normalized schema (with related tables) is possible if needed.
