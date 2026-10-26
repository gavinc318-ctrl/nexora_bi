"""Content hash of a release: the identity of an immutable release directory (LD-08 7.2 · DD-85 note).

Usage:  python3 release_hash.py <name>=<path> [<name>=<path> ...]

Prints one line: the SHA-256 over the sorted list of (name, SHA-256 of the file's bytes). <name> is the file's place
inside the release directory (e.g. contracts/release.yml, dags/oss911_ingest.py), <path> is where it is read from.
Run with the same names on the control node (paths into the repository) and on the target (paths into the release
directory): the two results are equal exactly when the directory holds this release, byte for byte. Standard
library only -- it runs with the system Python on both sides.
"""

import hashlib
import sys


def main(argv):
    pairs = []
    for arg in argv[1:]:
        name, sep, path = arg.partition("=")
        if not sep or not name or not path:
            print(f"expected <name>=<path>, got {arg!r}", file=sys.stderr)
            return 1
        with open(path, "rb") as fh:
            pairs.append((name, hashlib.sha256(fh.read()).hexdigest()))
    if not pairs:
        print("no files", file=sys.stderr)
        return 1
    if len({n for n, _ in pairs}) != len(pairs):
        print("a name is given twice", file=sys.stderr)
        return 1
    total = hashlib.sha256("".join(f"{n}\0{h}\n" for n, h in sorted(pairs)).encode("utf-8")).hexdigest()
    print(total)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
