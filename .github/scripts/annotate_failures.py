"""Emit GitHub annotations for failed pytest cases from a JUnit XML report.

Job logs are not always retrievable, but annotations are: they surface the failing
test id and the first lines of the failure directly on the pull request.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "/tmp/junit.xml")
    if not path.is_file():
        print("::error::junit report not found; pytest did not finish")
        return 0
    tree = ET.parse(path)
    failures = 0
    for case in tree.iter("testcase"):
        problems = [p for p in case if p.tag in {"failure", "error"}]
        if not problems:
            continue
        failures += 1
        node = problems[0]
        message = (node.get("message") or "").replace("\n", " ").replace("\r", " ")[:400]
        detail = " ".join((node.text or "").strip().splitlines()[-3:])[:300]
        name = f"{case.get('classname')}::{case.get('name')}"
        print(f"::error title={name}::{message} {detail}".rstrip())
    print(f"{failures} failing case(s) annotated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
