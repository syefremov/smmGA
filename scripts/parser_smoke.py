"""Synthetic parser check inside the disposable CI worker image; no owner-server operation."""

import json
import os
import subprocess
import sys

from tests.file_fixtures import docx, pdf


def main() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "linux":
        raise SystemExit("Disposable Linux CI only")
    code = (
        "import asyncio,sys,json; from smm_gpt.services.file_parser import SandboxedParser; "
        "result=asyncio.run(SandboxedParser().parse(sys.stdin.buffer.read(),sys.argv[1])); "
        "print(json.dumps({'text':result.text}))"
    )
    for format, value, expected in (("docx", docx(), "Крем"), ("pdf", pdf(), "ALPHA-42")):
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "worker", "python", "-c", code, format],
            input=value,
            capture_output=True,
            timeout=30,
            check=True,
        )
        if expected not in json.loads(result.stdout)["text"]:
            raise SystemExit("Synthetic extraction mismatch")
    print("Linux worker image: isolated PDF/DOCX parsing passed")


if __name__ == "__main__":
    main()
