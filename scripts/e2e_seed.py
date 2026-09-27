"""Prepara o banco de desenvolvimento para os testes E2E do portal (Playwright).

Só para desenvolvimento/CI: recusa APP_ENV=production. Usa os fluxos reais do produto:
- usuário e2e@dati.local (administrador da revenda) com a senha recebida em DM_E2E_PASSWORD
  (gerada a cada execução pelo global-setup do Playwright; nunca gravada em arquivo nem impressa);
- cliente/local "E2E" criados pela API;
- um coletor cadastrado pela API, instalado pelo endpoint de enroll e enviando leituras assinadas
  (o mesmo caminho do dm-agent), para o parque ter equipamentos com contadores e suprimentos.

Uso: DM_E2E_PASSWORD=... .venv\\Scripts\\python scripts\\e2e_seed.py [--api http://127.0.0.1:8000]
Saída (stdout, JSON): {"email": ..., "device_serial": ..., "customer": ...}
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import secrets
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import make_engine, make_sessionmaker  # noqa: E402
from app.core.permissions import RESELLER_ADMIN  # noqa: E402
from app.core.security import agent_signature, derive_agent_key, hash_password  # noqa: E402
from app.models import Reseller, User  # noqa: E402
from app.services.bootstrap import ensure_bootstrap  # noqa: E402

E2E_EMAIL = "e2e@dati.local"
COMPANY = "Empresa E2E"
CUSTOMER = "Cliente E2E"
SITE = "Local E2E"
REAL_SITE = "Local E2E coletor real"
REAL_COUNTERS_MINUTES = 5
AGENT = "Coletor E2E"
SERIAL = "E2E-0001"
MIN_PASSWORD = 12


async def ensure_user(password: str) -> None:
    settings = get_settings()
    if settings.app_env == "production":
        raise SystemExit("e2e_seed.py é só para desenvolvimento (APP_ENV=production)")
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            await ensure_bootstrap(session, settings)
            reseller = (
                (
                    await session.execute(
                        select(Reseller).where(Reseller.deleted_at.is_(None)).order_by(Reseller.created_at)
                    )
                )
                .scalars()
                .first()
            )
            if reseller is None:
                raise SystemExit("Nenhuma revenda no banco (o bootstrap deveria ter criado)")
            user = (await session.execute(select(User).where(User.email == E2E_EMAIL))).scalars().first()
            if user is None:
                user = User(
                    reseller_id=reseller.id,
                    name="Usuário E2E",
                    email=E2E_EMAIL,
                    password_hash="",
                )
                session.add(user)
            user.password_hash = hash_password(password)
            user.role_code = RESELLER_ADMIN
            user.customer_id = None
            user.active = True
            user.deleted_at = None
            user.must_change_password = False
            user.password_changed_at = datetime.now(UTC)
            user.failed_login_count = 0
            user.locked_until = None
            user.totp_enabled = False
            user.totp_secret_enc = None
            await session.commit()
    finally:
        await engine.dispose()


def check(resp: httpx.Response) -> Any:
    if resp.is_error:
        raise SystemExit(
            f"{resp.request.method} {resp.request.url.path} → HTTP {resp.status_code}: {resp.text}"
        )
    return resp.json() if resp.content else None


def supply(color: str, percent: float, key: str) -> dict[str, Any]:
    return {
        "key": key,
        "description": f"Toner {color}",
        "type": "toner",
        "class": "consumed",
        "color": color,
        "level": int(percent),
        "max_capacity": 100,
        "percent": percent,
        "level_state": "ok",
        "unit": "percent",
    }


async def ensure_site(client: httpx.AsyncClient, customer_id: str, name: str) -> dict[str, Any]:
    sites = check(await client.get("/api/v1/sites", params={"customer_id": customer_id, "limit": 50}))[
        "items"
    ]
    site = next((s for s in sites if s["name"] == name), None)
    if site is None:
        site = check(
            await client.post(
                "/api/v1/sites",
                json={"customer_id": customer_id, "name": name, "timezone": "America/Sao_Paulo"},
            )
        )
    return dict(site)


async def prepare_real_site(client: httpx.AsyncClient, customer_id: str) -> dict[str, Any]:
    """Local do E2E com dm-agent real: sem coletor e sem faixas (o teste cadastra tudo pelo portal).

    Leituras de 5 em 5 min: a janela do anti-duplicidade (metade do intervalo) fica em 2,5 min, então a
    leitura do coletor novo não é descartada por causa do coletor da execução anterior.
    """
    site = await ensure_site(client, customer_id, REAL_SITE)
    check(
        await client.patch(
            f"/api/v1/sites/{site['id']}",
            json={"collection_config": {"counters_minutes": REAL_COUNTERS_MINUTES}},
        )
    )
    agents = check(await client.get("/api/v1/agents", params={"site_id": site["id"], "limit": 50}))["items"]
    for a in agents:
        check(await client.delete(f"/api/v1/agents/{a['id']}"))
    for r in check(await client.get(f"/api/v1/sites/{site['id']}/ip-ranges")):
        check(await client.delete(f"/api/v1/ip-ranges/{r['id']}"))
    return site


async def seed_park(api: str, password: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=api, timeout=30) as client:
        token = check(
            await client.post("/api/v1/auth/login", json={"email": E2E_EMAIL, "password": password})
        )["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"

        customers = check(await client.get("/api/v1/customers", params={"q": CUSTOMER, "limit": 50}))["items"]
        customer = next((c for c in customers if c["name"] == CUSTOMER), None)
        if customer is None:
            companies = check(await client.get("/api/v1/companies", params={"limit": 500}))["items"]
            company = next((c for c in companies if c["legal_name"] == COMPANY), None)
            if company is None:
                company = check(await client.post("/api/v1/companies", json={"legal_name": COMPANY}))
            customer = check(
                await client.post("/api/v1/customers", json={"name": CUSTOMER, "company_id": company["id"]})
            )
        site = await ensure_site(client, customer["id"], SITE)
        real_site = await prepare_real_site(client, customer["id"])

        park = check(await client.get("/api/v1/park", params={"q": SERIAL, "limit": 5}))["items"]
        row = next((r for r in park if r["serial"] == SERIAL), None)
        last = (
            {"total": row["last_total"], "mono": row["last_mono"], "color": row["last_color"]}
            if row and row["last_total"] is not None
            else None
        )

        # O mesmo coletor em todas as execuções (reinstalação com código novo): outro coletor lendo a
        # mesma impressora no mesmo intervalo teria a leitura descartada pelo anti-duplicidade do cluster.
        agents = check(await client.get("/api/v1/agents", params={"site_id": site["id"], "limit": 50}))[
            "items"
        ]
        existing = next((a for a in agents if a["name"] == AGENT and a["revoked_at"] is None), None)
        if existing is None:
            code = check(
                await client.post(
                    "/api/v1/agents",
                    json={"site_id": site["id"], "name": AGENT, "kind": "windows", "priority": 100},
                )
            )["enrollment"]["code"]
        else:
            code = check(await client.post(f"/api/v1/agents/{existing['id']}/enrollment-code"))["code"]
        enrolled = check(
            await client.post(
                "/api/agent/enroll",
                json={
                    "v": 1,
                    "code": code,
                    "hostname": "PC-E2E",
                    "os": "Windows 11",
                    "arch": "amd64",
                    "kind": "windows",
                    "version": "1.0.0-e2e",
                    "local_ips": ["192.168.77.10"],
                },
            )
        )

    agent_id = enrolled["agent_id"]
    key = derive_agent_key(base64.b64decode(enrolled["secret"]))
    async with httpx.AsyncClient(base_url=api, timeout=30) as agent_client:
        # Mesmo protocolo do dm-agent: token por HMAC do segredo, heartbeat e lote de leituras.
        ts, nonce = int(time.time()), secrets.token_hex(16)
        token = check(
            await agent_client.post(
                "/api/agent/token",
                json={
                    "v": 1,
                    "agent_id": agent_id,
                    "ts": ts,
                    "nonce": nonce,
                    "signature": agent_signature(key, agent_id, ts, nonce),
                },
            )
        )["access_token"]
        agent_client.headers["Authorization"] = f"Bearer {token}"
        now = datetime.now(UTC)
        check(
            await agent_client.post(
                "/api/agent/heartbeat",
                json={
                    "v": 1,
                    "ts": now.isoformat(),
                    "version": "1.0.0-e2e",
                    "hostname": "PC-E2E",
                },
            )
        )

        def item(kind: str, read_at: datetime, **payload: Any) -> dict[str, Any]:
            return {
                "key": f"{agent_id}:{uuid.uuid4()}",  # única por execução: chave repetida = "duplicate"
                "kind": kind,
                "read_at": read_at.isoformat(),
                "device": {
                    "ip": "192.168.77.21",
                    "port": 161,
                    "serial": SERIAL,
                    "mac": "00:E2:E0:00:00:01",
                    "hostname": "IMP-E2E",
                    "sys_object_id": "1.3.6.1.4.1.18334.1.2",
                    "model": "bizhub C287",
                    "profile_key": "konica-minolta",
                },
                **payload,
            }

        def reading(counters: dict[str, int], read_at: datetime) -> dict[str, Any]:
            return item(
                "reading",
                read_at,
                reading={
                    "counters": counters,
                    "counter_source": "konica_counters",
                    "profile_key": "konica-minolta",
                    "profile_version": 1,
                    "mono_only": False,
                    "sum_tolerance_percent": 2,
                    "status": "ready",
                    "error_bits": 0,
                    "source": "snmp",
                },
            )

        # Leituras são imutáveis e precisam avançar: continua dos contadores atuais do equipamento.
        base = {"total": 120000, "mono": 70000, "color": 50000}
        items: list[dict[str, Any]] = []
        if last is None:
            items.append(reading(base, now - timedelta(days=1)))
        else:
            base = last
        items += [
            reading(
                {"total": base["total"] + 450, "mono": base["mono"] + 300, "color": base["color"] + 150}, now
            ),
            item(
                "supplies",
                now,
                supplies=[
                    supply("black", 42, "1.1"),
                    supply("cyan", 64, "1.2"),
                    supply("magenta", 8, "1.3"),
                    supply("yellow", 77, "1.4"),
                ],
            ),
        ]
        results = check(await agent_client.post("/api/agent/readings", json={"v": 1, "items": items}))[
            "results"
        ]
        rejected = [r for r in results if r["status"] != "accepted"]
        if rejected:
            raise SystemExit(f"Leituras recusadas pela API: {rejected}")
    return {
        "email": E2E_EMAIL,
        "device_serial": SERIAL,
        "customer": CUSTOMER,
        "customer_id": customer["id"],
        "real_site_id": real_site["id"],
        "total": base["total"] + 450,
    }


async def main_async(api: str) -> int:
    password = os.environ.get("DM_E2E_PASSWORD", "")
    if len(password) < MIN_PASSWORD:
        raise SystemExit(
            "Defina DM_E2E_PASSWORD (12+ caracteres); o global-setup do Playwright gera uma aleatória"
        )
    await ensure_user(password)
    result = await seed_park(api, password)
    print(json.dumps(result, ensure_ascii=False))  # noqa: T201
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args.api))


if __name__ == "__main__":
    raise SystemExit(main())
