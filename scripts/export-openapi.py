"""Export a deterministic OpenAPI contract for the generated browser client."""

import json
from pathlib import Path

from smm_gpt.application import app


def main() -> None:
    output = Path("openapi.json")
    output.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
