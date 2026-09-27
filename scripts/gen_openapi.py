"""Gera frontend/src/api/openapi.json a partir da API (fonte do cliente TypeScript do portal).

Uso:  .venv\\Scripts\\python scripts\\gen_openapi.py          (grava)
      .venv\\Scripts\\python scripts\\gen_openapi.py --check  (falha se estiver desatualizado)
Depois de gravar: cd frontend; npm run gen:api  (gera src/api/schema.d.ts com openapi-typescript).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.api.main import create_app  # noqa: E402
from app.core.config import get_settings  # noqa: E402

OUT = REPO / "frontend" / "src" / "api" / "openapi.json"


def render() -> str:
    app = create_app(get_settings(), run_bootstrap=False)
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    content = render()
    current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if args.check:
        if current != content:
            print(f"desatualizado: {OUT.relative_to(REPO)} (rode scripts/gen_openapi.py e npm run gen:api)")  # noqa: T201
            return 1
        return 0
    OUT.write_text(content, encoding="utf-8", newline="\n")
    print(f"{OUT.relative_to(REPO)} gravado")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
