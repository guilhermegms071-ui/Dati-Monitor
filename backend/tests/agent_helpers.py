"""Helpers that play the role of dm-agent against the real API (enroll, sign, send batches)."""

import base64
import gzip
import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.security import agent_signature, derive_agent_key
from tests.conftest import Tenant, auth, login


@dataclass
class FakeAgent:
    client: httpx.AsyncClient
    agent_id: str
    secret: bytes
    token: str = ""
    seq: int = 0
    site_id: str = ""
    sent: list[dict[str, Any]] = field(default_factory=list)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def token_request(
        self, *, ts: int | None = None, nonce: str | None = None, key: bytes | None = None
    ) -> dict[str, Any]:
        ts = ts if ts is not None else int(time.time())
        nonce = nonce or secrets.token_hex(16)
        sig = agent_signature(key or derive_agent_key(self.secret), self.agent_id, ts, nonce)
        return {"v": 1, "agent_id": self.agent_id, "ts": ts, "nonce": nonce, "signature": sig}

    async def authenticate(self) -> None:
        resp = await self.client.post("/api/agent/token", json=self.token_request())
        assert resp.status_code == 200, resp.text
        self.token = resp.json()["access_token"]

    async def heartbeat(self, **extra: Any) -> dict[str, Any]:
        body = {
            "v": 1,
            "ts": datetime.now(UTC).isoformat(),
            "version": "1.0.0-test",
            "hostname": "PC-TESTE",
            **extra,
        }
        resp = await self.client.post("/api/agent/heartbeat", json=body, headers=self.headers)
        assert resp.status_code == 200, resp.text
        data: dict[str, Any] = resp.json()
        return data

    async def watchdog_heartbeat(self, **extra: Any) -> dict[str, Any]:
        """What dm-watchdog sends every 60 s (same credential as the agent)."""
        body = {
            "v": 1,
            "ts": datetime.now(UTC).isoformat(),
            "version": "1.0.0-test",
            "os": "windows",
            "arch": "amd64",
            "agent_state": "running",
            "agent_healthy": True,
            **extra,
        }
        resp = await self.client.post("/api/watchdog/heartbeat", json=body, headers=self.headers)
        assert resp.status_code == 200, resp.text
        data: dict[str, Any] = resp.json()
        return data

    async def report(self, command_id: str, state: str, **extra: Any) -> dict[str, Any]:
        resp = await self.client.post(
            f"/api/agent/commands/{command_id}/update",
            json={"v": 1, "id": command_id, "state": state, **extra},
            headers=self.headers,
        )
        assert resp.status_code == 200, resp.text
        data: dict[str, Any] = resp.json()
        return data

    def item(
        self,
        kind: str,
        *,
        serial: str,
        ip: str = "10.0.0.5",
        port: int = 161,
        read_at: datetime | None = None,
        **payload: Any,
    ) -> dict[str, Any]:
        self.seq += 1
        it: dict[str, Any] = {
            "key": f"{self.agent_id}:{self.seq}",
            "kind": kind,
            "read_at": (read_at or datetime.now(UTC)).isoformat(),
            "device": {
                "ip": ip,
                "port": port,
                "serial": serial,
                "mac": "00:AA:00:00:00:01",
                "hostname": "IMP",
                "sys_object_id": "1.3.6.1.4.1.18334.1.2",
                "model": "bizhub C287",
                "profile_key": "konica-minolta",
            },
            **payload,
        }
        return it

    def reading(self, serial: str, counters: dict[str, int], **kw: Any) -> dict[str, Any]:
        mono_only = kw.pop("mono_only", False)
        tolerance = kw.pop("tolerance", 2)
        return self.item(
            "reading",
            serial=serial,
            reading={
                "counters": counters,
                "counter_source": "konica_counters",
                "profile_key": "konica-minolta",
                "profile_version": 1,
                "mono_only": mono_only,
                "sum_tolerance_percent": tolerance,
                "status": "ready",
                "error_bits": 0,
                "source": "snmp",
            },
            **kw,
        )

    async def send(self, items: list[dict[str, Any]], *, gz: bool = True) -> list[dict[str, Any]]:
        raw = json.dumps({"v": 1, "items": items}).encode()
        headers = {**self.headers, "Content-Type": "application/json"}
        if gz:
            raw = gzip.compress(raw)
            headers["Content-Encoding"] = "gzip"
        resp = await self.client.post("/api/agent/readings", content=raw, headers=headers)
        assert resp.status_code == 200, resp.text
        results: list[dict[str, Any]] = resp.json()["results"]
        return results


async def create_agent(
    client: httpx.AsyncClient, admin_token: str, site_id: uuid.UUID | str, name: str = "Coletor 1"
) -> dict[str, Any]:
    resp = await client.post(
        "/api/v1/agents", json={"site_id": str(site_id), "name": name}, headers=auth(admin_token)
    )
    assert resp.status_code == 201, resp.text
    data: dict[str, Any] = resp.json()
    return data


async def enrolled_agent(client: httpx.AsyncClient, tenant: Tenant, name: str = "Coletor 1") -> FakeAgent:
    admin = await login(client, tenant.admin_email)
    created = await create_agent(client, admin, tenant.site_id, name)
    code = created["enrollment"]["code"]
    resp = await client.post(
        "/api/agent/enroll",
        json={
            "v": 1,
            "code": code,
            "hostname": "PC-TESTE",
            "os": "Windows 11",
            "arch": "amd64",
            "kind": "windows",
            "version": "1.0.0-test",
            "local_ips": ["192.168.10.20"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    agent = FakeAgent(client, body["agent_id"], base64.b64decode(body["secret"]), site_id=str(tenant.site_id))
    await agent.authenticate()
    return agent
