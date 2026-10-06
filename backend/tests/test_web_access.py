"""Acesso remoto à página web da impressora (PROMPT 4.9 / critério 14): who may open, refusal of an IP that
is not a registered printer (audited), the tunnel end to end through the real gateway with a WebSocket
client playing the collector, URL/cookie rewriting and the sandbox isolation."""

import asyncio
import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.client import ClientConnection

from app.gateway import devweb
from app.models import AgentPresence, AuditLog, Command, WebSession
from tests.agent_helpers import FakeAgent, enrolled_agent
from tests.conftest import Factory, Tenant, auth, login
from tests.test_gateway import gateway, recv, send, ws_connect  # noqa: F401 - fixture

PREFIX = "/devweb/TOKEN/"
ORIGINS = devweb.origins("10.0.0.5", 80, "http")


# ----------------------------------------------------------------------------- reescrita (unidade)


def test_rewrite_urls_and_cookies() -> None:
    assert devweb.rewrite_url("http://10.0.0.5/a/b?x=1", PREFIX, ORIGINS) == PREFIX + "a/b?x=1"
    assert devweb.rewrite_url("http://10.0.0.5:80/", PREFIX, ORIGINS) == PREFIX
    assert devweb.rewrite_url("/wcd/login.cgi", PREFIX, ORIGINS) == PREFIX + "wcd/login.cgi"
    assert devweb.rewrite_url("http://outro.host/x", PREFIX, ORIGINS) == "http://outro.host/x"
    assert devweb.rewrite_url("rel/x.html", PREFIX, ORIGINS) == "rel/x.html"
    assert devweb.rewrite_url(PREFIX + "ja", PREFIX, ORIGINS) == PREFIX + "ja"
    cookie = devweb.rewrite_set_cookie(
        "ID=abc; Path=/wcd; Domain=10.0.0.5; HttpOnly; SameSite=Strict", PREFIX
    )
    assert cookie == f"ID=abc; HttpOnly; Path={PREFIX}wcd; SameSite=None; Secure"
    assert devweb.rewrite_set_cookie("s=1", PREFIX) == f"s=1; Path={PREFIX}; SameSite=None; Secure"


def test_rewrite_html_and_css() -> None:
    page = (
        "<html><head><title>x</title></head><body>"
        '<a href="/status.html">s</a> <img src=/logo.png> <form action="/login"></form>'
        '<a href="http://10.0.0.5/abs">a</a> <a href="//cdn.example/x">cdn</a> <a href="rel.html">r</a>'
        '<meta http-equiv=refresh content="5; url=/next"><div style="background:url(\'/bg.png\')"></div>'
        "</body></html>"
    )
    out = devweb.rewrite_body(page, "text/html; charset=utf-8", PREFIX, ORIGINS)
    for expected in (
        f'href="{PREFIX}status.html"',
        f"src={PREFIX}logo.png",
        f'action="{PREFIX}login"',
        f'href="{PREFIX}abs"',
        'href="//cdn.example/x"',
        'href="rel.html"',
        f"url={PREFIX}next",
        f"url('{PREFIX}bg.png')",
    ):
        assert expected in out, expected
    assert out.index("<script>") > out.index("<head>"), "script de correção logo no começo do <head>"
    css = devweb.rewrite_body("a{background:url(/i.png)} @import '/b.css';", "text/css", PREFIX, ORIGINS)
    assert css == f"a{{background:url({PREFIX}i.png)}} @import '{PREFIX}b.css';"


# ----------------------------------------------------------------------------- API do portal


async def _setup(client: httpx.AsyncClient, factory: Factory) -> tuple[Tenant, str, FakeAgent, str]:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    agent = await enrolled_agent(client, tenant)
    await agent.send([agent.reading("WEB1", {"total": 10, "mono": 10, "color": 0}, ip="10.0.0.5")])
    park = (await client.get("/api/v1/park", params={"serial": "WEB1"}, headers=auth(admin))).json()
    return tenant, admin, agent, park["items"][0]["id"]


async def _presence(maker: async_sessionmaker[AsyncSession], agent: FakeAgent) -> None:
    now = datetime.now(UTC)
    async with maker() as s:
        s.add(
            AgentPresence(
                agent_id=uuid.UUID(agent.agent_id),
                reseller_id=await _reseller(s, agent),
                gateway_id="teste",
                connected_at=now,
                last_seen_at=now,
            )
        )
        await s.commit()


async def _reseller(s: AsyncSession, agent: FakeAgent) -> uuid.UUID:
    from app.models import Agent  # noqa: PLC0415

    row = await s.get(Agent, uuid.UUID(agent.agent_id))
    assert row is not None
    return row.reseller_id


async def test_open_rules_permissions_and_denied_ip(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tenant, admin, agent, device_id = await _setup(client, factory)
    url = f"/api/v1/devices/{device_id}/web-session"
    # Sem coletor conectado ao gateway: não abre.
    resp = await client.post(url, json={"port": 80}, headers=auth(admin))
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "no_agent_online"
    await _presence(sessionmaker, agent)
    bad_port = await client.post(url, json={"port": 22}, headers=auth(admin))
    assert bad_port.json()["detail"]["code"] == "web_port_not_allowed"
    ok = await client.post(url, json={"port": 80}, headers=auth(admin))
    assert ok.status_code == 201, ok.text
    out = ok.json()
    assert out["url"].startswith("/devweb/")
    assert "?dm_key=" in out["url"]
    assert (out["ip"], out["port"], out["agent_name"]) == ("10.0.0.5", 80, "Coletor 1")
    async with sessionmaker() as s:
        ws = (await s.execute(select(WebSession))).scalar_one()
        token = out["url"].split("/")[2]
        assert token not in (ws.token_hash, ws.key_hash), "banco guarda só hashes"
        cmd = await s.get(Command, ws.command_id)
        assert cmd is not None
        assert cmd.type == "web_proxy_open"
        assert cmd.params["ip"] == "10.0.0.5"
        assert cmd.params["port"] == 80

    # Operador não tem a permissão especial de acesso web (técnico+).
    _, op_email = await factory.user(tenant.reseller_id, role="operator")
    operator = await login(client, op_email)
    assert (await client.post(url, json={"port": 80}, headers=auth(operator))).status_code == 403
    _, tech_email = await factory.user(tenant.reseller_id, role="technician")
    tech = await login(client, tech_email)
    assert (await client.post(url, json={"port": 8080}, headers=auth(tech))).status_code == 201

    # Por IP: só impressora cadastrada no local. A recusa é auditada (critério 14).
    by_ip = f"/api/v1/sites/{tenant.site_id}/web-session"
    denied = await client.post(by_ip, json={"ip": "10.0.0.99", "port": 80}, headers=auth(tech))
    assert denied.status_code == 403
    assert "não é de uma impressora cadastrada" in denied.json()["detail"]["message"]
    allowed = await client.post(
        by_ip, json={"ip": "10.0.0.5", "port": 443, "scheme": "https"}, headers=auth(tech)
    )
    assert allowed.status_code == 201
    async with sessionmaker() as s:
        audit = {
            (a.action, (a.after or {}).get("reason"))
            for a in (
                await s.execute(select(AuditLog).where(AuditLog.action.like("web_session.%")))
            ).scalars()
        }
    assert ("web_session.denied", "ip_not_registered") in audit
    assert ("web_session.open", None) in audit

    other = await factory.tenant("Outra empresa")
    stranger = await login(client, other.admin_email)
    assert (await client.post(url, json={"port": 80}, headers=auth(stranger))).status_code == 404


# ----------------------------------------------------------------------------- túnel de ponta a ponta

PAGE = (
    "<html><head><title>Impressora</title></head><body><h1>Contador total: 217.031</h1>"
    '<a href="/status.html">Status</a><img src="http://10.0.0.5/logo.png"></body></html>'
)


async def fake_collector(ws: ClientConnection, seen: list[dict[str, Any]]) -> None:
    """Plays the collector: confirms web_proxy_open and answers every web_request."""
    async for raw in ws:
        msg = json.loads(raw)
        seen.append({"type": msg["type"], "path": None, "data": msg["data"]})
        if msg["type"] == "command":
            await send(
                ws, "command_update", {"v": 1, "id": msg["data"]["id"], "state": "succeeded", "result": {}}
            )
        elif msg["type"] == "web_request":
            req = msg["data"]
            seen.append({**req, "type": "web_request"})
            sid = req["stream_id"]
            if req["path"] == "/":
                headers = [["Content-Type", "text/html; charset=utf-8"], ["Set-Cookie", "SESS=abc; Path=/"],
                           ["X-Frame-Options", "DENY"], ["Content-Security-Policy", "default-src *"]]  # fmt: skip
                await send(ws, "web_response", {"v": 1, "stream_id": sid, "status": 200, "headers": headers})
                body = PAGE.encode()
                for part in (body[:40], body[40:]):
                    data = base64.b64encode(part).decode()
                    await send(ws, "web_chunk", {"v": 1, "stream_id": sid, "data_b64": data, "end": False})
                await send(ws, "web_chunk", {"v": 1, "stream_id": sid, "data_b64": "", "end": True})
            elif req["path"] == "/login":
                loc = [["Location", "http://10.0.0.5/"]]
                await send(ws, "web_response", {"v": 1, "stream_id": sid, "status": 302, "headers": loc})
                await send(ws, "web_chunk", {"v": 1, "stream_id": sid, "end": True})
            else:
                await send(
                    ws, "web_error", {"v": 1, "stream_id": sid, "message": "caminho não existe na impressora"}
                )


@pytest.fixture
def http_gateway(gateway: str) -> str:  # noqa: F811
    return gateway.replace("ws://", "http://").removesuffix("/ws/agent")


async def browse(http_gateway: str, link: str) -> None:
    """Plays the browser: binds the link, opens the page, follows the redirect, tries another browser."""
    prefix = link.split("?", maxsplit=1)[0]
    async with httpx.AsyncClient(base_url=http_gateway, timeout=20) as browser:
        first = await browser.get(link)
        assert first.status_code == 303
        assert first.headers["location"] == prefix
        assert "dm_devweb=" in first.headers["set-cookie"]
        cookie = first.headers["set-cookie"].split(";")[0]
        page = await browser.get(prefix, headers={"Cookie": cookie})
        assert page.status_code == 200, page.text
        assert "Contador total: 217.031" in page.text
        assert f'href="{prefix}status.html"' in page.text
        assert f'src="{prefix}logo.png"' in page.text
        assert page.headers["content-security-policy"].startswith("sandbox allow-scripts")
        assert "allow-same-origin" not in page.headers["content-security-policy"]
        assert page.headers["x-frame-options"] == "SAMEORIGIN"
        assert page.headers["set-cookie"] == f"SESS=abc; Path={prefix}; SameSite=None; Secure"

        redirect = await browser.post(prefix + "login", content=b"u=a", headers={"Cookie": cookie})
        assert (redirect.status_code, redirect.headers["location"]) == (302, prefix)
        missing = await browser.get(prefix + "nada", headers={"Cookie": cookie})
        assert missing.status_code == 502
        assert "caminho não existe na impressora" in missing.text

        # O mesmo link em outro navegador (sem o cookie) não abre; token errado também não.
        assert (await browser.get(link)).status_code == 403
        assert (await browser.get(prefix)).status_code == 403
        # Portal aberto por http num IP da rede: o navegador descarta o cookie Secure; a tela diz o motivo.
        lan = await browser.get(prefix, headers={"Host": "10.10.10.25:5173"})
        assert lan.status_code == 403
        assert "https ou pelo endereço localhost" in lan.text
        tls = await browser.get(
            prefix, headers={"Host": "monitor.exemplo.com.br", "X-Forwarded-Proto": "https"}
        )
        assert "pertence a outro navegador" in tls.text
        assert (await browser.get("/devweb/token-errado/")).status_code == 404


async def test_tunnel_end_to_end(
    client: httpx.AsyncClient,
    factory: Factory,
    sessionmaker: async_sessionmaker[AsyncSession],
    gateway: str,  # noqa: F811
    http_gateway: str,
) -> None:
    _, admin, agent, device_id = await _setup(client, factory)
    seen: list[dict[str, Any]] = []
    async with await ws_connect(gateway, agent) as ws:
        await recv(ws, "welcome")  # presença já gravada pelo gateway
        worker = asyncio.create_task(fake_collector(ws, seen))
        try:
            opened = await client.post(
                f"/api/v1/devices/{device_id}/web-session", json={"port": 80}, headers=auth(admin)
            )
            assert opened.status_code == 201, opened.text
            link = opened.json()["url"]
            for _ in range(100):
                async with sessionmaker() as s:
                    state = (
                        await s.execute(select(Command.state).where(Command.type == "web_proxy_open"))
                    ).scalar_one()
                if state == "succeeded":
                    break
                await asyncio.sleep(0.1)
            assert state == "succeeded", (state, [m.get("type") for m in seen])
            await browse(http_gateway, link)
        finally:
            worker.cancel()
    login_req = next(r for r in seen if r.get("path") == "/login")
    assert login_req["method"] == "POST"
    assert base64.b64decode(login_req["body_b64"]) == b"u=a"
    sent_headers = {k.lower(): v for k, v in login_req["headers"]}
    assert sent_headers["origin"] == "http://10.0.0.5"
    assert "dm_devweb" not in sent_headers.get("cookie", "")
    async with sessionmaker() as s:
        ws_row = (await s.execute(select(WebSession))).scalar_one()
        assert ws_row.requests == 2  # página e login; o pedido com erro não chegou a ter corpo
        assert ws_row.bytes_out >= len(PAGE)
        assert ws_row.bound_at is not None
