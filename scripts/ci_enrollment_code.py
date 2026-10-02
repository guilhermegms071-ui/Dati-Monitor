"""Código de cadastro para testar os instaladores (CI e desenvolvimento; recusa APP_ENV=production).

Roda o scripts\\e2e_seed.py com uma senha aleatória (só em memória), cria um coletor novo pela API no local
"E2E" e imprime só o código de 8 caracteres.

Uso:  .venv\\Scripts\\python scripts\\ci_enrollment_code.py [--api http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    if os.environ.get("APP_ENV") == "production":
        sys.stderr.write("ERRO: script só para desenvolvimento/CI\n")
        return 2
    password = "Ci-" + secrets.token_urlsafe(12)
    seed = subprocess.run(  # noqa: S603 - script do próprio repositório
        [sys.executable, str(REPO / "scripts" / "e2e_seed.py"), "--api", args.api],
        env={**os.environ, "DM_E2E_PASSWORD": password, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if seed.returncode != 0:
        sys.stderr.write(f"ERRO: e2e_seed falhou:\n{seed.stderr}\n")
        return 1
    info = json.loads(seed.stdout.strip().splitlines()[-1])
    with httpx.Client(base_url=args.api, timeout=30) as c:
        login = c.post("/api/v1/auth/login", json={"email": info["email"], "password": password})
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created = c.post(
            "/api/v1/agents",
            json={"site_id": info["real_site_id"], "name": "Coletor instalador"},
            headers=headers,
        )
        created.raise_for_status()
    sys.stdout.write(created.json()["enrollment"]["code"] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
