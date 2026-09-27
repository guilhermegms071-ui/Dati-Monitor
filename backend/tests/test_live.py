"""Portal live events (SSE) end to end: real uvicorn API, a real streaming client, events coming from
PostgreSQL LISTEN/NOTIFY and filtered by tenant scope."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import uvicorn

from app.api.main import create_app
from app.core.config import Settings
from tests.agent_helpers import enrolled_agent
from tests.conftest import Factory, auth, free_port, login


@pytest.fixture
async def live_api(test_settings: Settings, clean_db: None) -> AsyncIterator[str]:
    port = free_port()
    app = create_app(test_settings)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started
    await asyncio.wait_for(app.state.live.connected.wait(), 10)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await asyncio.wait_for(task, 15)


class SSE:
    def __init__(self, resp: httpx.Response) -> None:
        self.lines = resp.aiter_lines()

    async def next(self, kind: str, wait_s: float = 10) -> dict[str, Any]:
        event = None
        async with asyncio.timeout(wait_s):
            async for line in self.lines:
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: ") and event == kind:
                    data: dict[str, Any] = json.loads(line[6:])
                    return data
        raise AssertionError(f"evento {kind} não chegou")


async def test_events_stream_live_and_scoped(live_api: str, factory: Factory) -> None:
    tenant = await factory.tenant()
    other = await factory.tenant("Revenda B")
    async with httpx.AsyncClient(base_url=live_api, timeout=30) as client:
        admin = await login(client, tenant.admin_email)
        other_admin = await login(client, other.admin_email)
        agent = await enrolled_agent(client, tenant)
        async with (
            client.stream("GET", "/api/v1/events", headers=auth(admin)) as mine,
            client.stream("GET", "/api/v1/events", headers=auth(other_admin)) as theirs,
        ):
            assert mine.status_code == 200
            assert mine.headers["content-type"].startswith("text/event-stream")
            a, b = SSE(mine), SSE(theirs)
            await a.next("hello")
            await b.next("hello")

            await agent.heartbeat()  # offline → online: evento do coletor
            ev = await a.next("agent")
            assert ev["id"] == agent.agent_id
            assert ev["state"] == "online"
            assert ev["cluster_role"] == "master"

            created = (
                await client.post(
                    f"/api/v1/agents/{agent.agent_id}/commands",
                    json={"type": "diagnostics"},
                    headers=auth(admin),
                )
            ).json()
            ev = await a.next("command")
            assert ev == {
                "type": "command",
                "reseller_id": str(tenant.reseller_id),
                "customer_id": str(tenant.customer_id),
                "id": created["id"],
                "agent_id": agent.agent_id,
                "state": "pending",
            }
            await agent.send([agent.reading("SER-9", {"total": 10, "mono": 10})])
            ev = await a.next("devices")
            assert ev["count"] == 1
            # A outra revenda não recebe nada disso.
            with pytest.raises(TimeoutError):
                await b.next("command", wait_s=1.5)

    unauth = httpx.AsyncClient(base_url=live_api)
    async with unauth:
        assert (await unauth.get("/api/v1/events")).status_code == 401
