"""Write the HTTP API's OpenAPI document to openapi/openapi.yaml.

Run from the project root after changing routes or models:
  python scripts/export_openapi.py
tests/test_openapi.py fails if the committed file is out of date.
"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

OUTPUT = ROOT / "openapi" / "openapi.yaml"


def main():
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(
        yaml.safe_dump(app.openapi(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
