import sys
import os
import subprocess

HEADER_EXTS = {".h", ".hpp"}
IMPL_EXTS = {".c", ".cpp", ".cxx"}

def usage():
    print("Usage: python route_parser.py <source_or_header_file> [options]", file=sys.stderr)
    print("Routes to header_parser.py or impl_parser.py based on file extension.", file=sys.stderr)
    sys.exit(2)

def main():
    if len(sys.argv) < 2:
        usage()

    file_path = sys.argv[1]
    if not os.path.isfile(file_path):
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    _, ext = os.path.splitext(file_path)
    ext = ext.lower()

    if ext in HEADER_EXTS:
        parser = os.path.join(os.path.dirname(__file__), "header_parser.py")
    elif ext in IMPL_EXTS:
        parser = os.path.join(os.path.dirname(__file__), "impl_parser.py")
    else:
        print(f"Error: Unsupported file extension: {ext}", file=sys.stderr)
        sys.exit(1)

    cmd = [sys.executable, parser] + sys.argv[1:]
    result = subprocess.run(cmd)
    sys.exit(result.returncode)

if __name__ == "__main__":
    main()
