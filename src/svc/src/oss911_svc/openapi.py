"""Write the query API's OpenAPI document, generated from the code (DD-99 · LD-04 10.2).

    oss911-svc-openapi              print it
    oss911-svc-openapi --check FILE exit 1 if FILE differs from what the code generates

The committed contracts/openapi/v1.json is the contract the front end builds on; the deployment runs --check against
it, so code and contract cannot drift apart unnoticed.
"""

from __future__ import annotations

import json
import sys

from .app import create_app


def document() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = document()
    if argv[:1] == ["--check"] and len(argv) == 2:
        with open(argv[1], encoding="utf-8") as fh:
            if fh.read() != text:
                print(f"{argv[1]} differs from the OpenAPI the code generates; regenerate it with oss911-svc-openapi "
                      f"and commit it", file=sys.stderr)
                return 1
        print(f"{argv[1]} matches the code")
        return 0
    if argv:
        print("usage: oss911-svc-openapi [--check FILE]", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
