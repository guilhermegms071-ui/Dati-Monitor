"""/api/v1 collectors (agents), IP ranges, SNMP credentials and devices (read)."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import PrincipalDep, SessionDep, SettingsDep
from app.core.product import get_product
from app.models import Agent, AgentEnrollmentCode
from app.schemas.collection import (
    AgentCreated,
    AgentIn,
    AgentOut,
    AgentStats,
    AgentUpdate,
    DeviceDetail,
    DeviceEventOut,
    DeviceOut,
    EnrollmentCodeOut,
    IpRangeIn,
    IpRangeOut,
    RangeImportIn,
    RangeImportOut,
    ReadingOut,
    SnmpCredentialIn,
    SnmpCredentialOut,
    SnmpCredentialUpdate,
    SupplyOut,
)
from app.schemas.common import ERROR_RESPONSES, Page
from app.services import agents as agents_svc
from app.services import devices as devices_svc
from app.services import presence as presence_svc
from app.services import site_config as site_svc
from app.services.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Direction
from app.services.watchdog import watchdog_alive

router = APIRouter(responses=ERROR_RESPONSES)
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


def enrollment_out(code: AgentEnrollmentCode, server_url: str) -> EnrollmentCodeOut:
    product = get_product()
    return EnrollmentCodeOut(
        code=code.code,
        expires_at=code.expires_at,
        install_command=f"dm-agent enroll --server {server_url} --code {code.code}",
        instructions=[
            f"1. Baixe o instalador do coletor {product.name} na página Downloads e execute-o num PC "
            "ligado o tempo todo, na mesma rede das impressoras (Windows 10 ou mais novo).",
            f"2. Quando o instalador pedir, informe o código {code.code} (válido por 7 dias, uso único).",
            "3. Pronto: o coletor aparece como online nesta tela em alguns segundos.",
        ],
    )


# ----------------------------------------------------------------------------- agents


async def agents_out(session: SessionDep, agents: Sequence[Agent]) -> list[AgentOut]:
    connected = await presence_svc.connected_ids(session, (a.id for a in agents))
    places = await agents_svc.site_names(session, {a.site_id for a in agents})
    out = []
    for a in agents:
        site_name, customer_id, customer_name = places.get(a.site_id, ("", None, ""))
        out.append(
            AgentOut.model_validate(a).model_copy(
                update={
                    "ws_connected": a.id in connected,
                    "watchdog_alive": watchdog_alive(a),
                    "site_name": site_name,
                    "customer_id": customer_id,
                    "customer_name": customer_name,
                }
            )
        )
    return out


async def agent_out(session: SessionDep, agent: Agent) -> AgentOut:
    return (await agents_out(session, [agent]))[0]


@router.get("/agents", response_model=Page[AgentOut], tags=["coletores"])
async def list_agents(
    p: PrincipalDep,
    session: SessionDep,
    site_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    state: str | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: str = "name",
    direction: Direction = "asc",
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> Page[AgentOut]:
    page = await agents_svc.list_agents(
        session,
        p,
        site_id=site_id,
        customer_id=customer_id,
        state=state,
        q=q,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )
    return Page(items=await agents_out(session, page.items), next_cursor=page.next_cursor)


@router.post("/agents", response_model=AgentCreated, status_code=status.HTTP_201_CREATED, tags=["coletores"])
async def create_agent(
    body: AgentIn, p: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> AgentCreated:
    agent, code = await agents_svc.create_agent(session, p, body)
    await session.commit()
    return AgentCreated(
        agent=await agent_out(session, agent), enrollment=enrollment_out(code, settings.public_server_url)
    )


@router.get("/agents/{agent_id}", response_model=AgentOut, tags=["coletores"])
async def get_agent(agent_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> AgentOut:
    return await agent_out(session, await agents_svc.get_agent(session, p, agent_id))


@router.patch("/agents/{agent_id}", response_model=AgentOut, tags=["coletores"])
async def update_agent(
    agent_id: uuid.UUID, body: AgentUpdate, p: PrincipalDep, session: SessionDep
) -> AgentOut:
    agent = await agents_svc.update_agent(session, p, agent_id, body)
    await session.commit()
    return await agent_out(session, agent)


@router.post("/agents/{agent_id}/enrollment-code", response_model=EnrollmentCodeOut, tags=["coletores"])
async def regenerate_code(
    agent_id: uuid.UUID, p: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> EnrollmentCodeOut:
    code = await agents_svc.regenerate_code(session, p, agent_id)
    await session.commit()
    return enrollment_out(code, settings.public_server_url)


@router.post("/agents/{agent_id}/revoke", response_model=AgentOut, tags=["coletores"])
async def revoke_agent(agent_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> AgentOut:
    agent = await agents_svc.revoke_agent(session, p, agent_id)
    await session.commit()
    return await agent_out(session, agent)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["coletores"])
async def delete_agent(agent_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> Response:
    await agents_svc.delete_agent(session, p, agent_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- IP ranges


@router.get("/sites/{site_id}/ip-ranges", response_model=list[IpRangeOut], tags=["locais"])
async def list_ranges(site_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> list[IpRangeOut]:
    return [IpRangeOut.model_validate(r) for r in await site_svc.list_ranges(session, p, site_id)]


@router.post(
    "/sites/{site_id}/ip-ranges",
    response_model=IpRangeOut,
    status_code=status.HTTP_201_CREATED,
    tags=["locais"],
)
async def create_range(
    site_id: uuid.UUID, body: IpRangeIn, p: PrincipalDep, session: SessionDep
) -> IpRangeOut:
    row = await site_svc.create_range(session, p, site_id, body)
    await session.commit()
    return IpRangeOut.model_validate(row)


@router.post(
    "/sites/{site_id}/ip-ranges/import",
    response_model=RangeImportOut,
    tags=["locais"],
    summary="Importar faixas, IPs e hostnames de um arquivo .txt (uma entrada por linha)",
)
async def import_ranges(
    site_id: uuid.UUID, body: RangeImportIn, p: PrincipalDep, session: SessionDep
) -> RangeImportOut:
    out = await site_svc.import_ranges(session, p, site_id, body.content, body.ports)
    await session.commit()
    return out


@router.put("/ip-ranges/{range_id}", response_model=IpRangeOut, tags=["locais"])
async def update_range(
    range_id: uuid.UUID, body: IpRangeIn, p: PrincipalDep, session: SessionDep
) -> IpRangeOut:
    row = await site_svc.update_range(session, p, range_id, body)
    await session.commit()
    return IpRangeOut.model_validate(row)


@router.post("/ip-ranges/{range_id}/approve", response_model=IpRangeOut, tags=["locais"])
async def approve_range(range_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> IpRangeOut:
    row = await site_svc.approve_range(session, p, range_id)
    await session.commit()
    return IpRangeOut.model_validate(row)


@router.delete("/ip-ranges/{range_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["locais"])
async def delete_range(range_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> Response:
    await site_svc.delete_range(session, p, range_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- SNMP credentials


@router.get("/sites/{site_id}/snmp-credentials", response_model=list[SnmpCredentialOut], tags=["locais"])
async def list_credentials(
    site_id: uuid.UUID, p: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> list[SnmpCredentialOut]:
    return [
        site_svc.credential_out(c, settings) for c in await site_svc.list_credentials(session, p, site_id)
    ]


@router.post(
    "/sites/{site_id}/snmp-credentials",
    response_model=SnmpCredentialOut,
    status_code=status.HTTP_201_CREATED,
    tags=["locais"],
)
async def create_credential(
    site_id: uuid.UUID, body: SnmpCredentialIn, p: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> SnmpCredentialOut:
    row = await site_svc.create_credential(session, settings, p, site_id, body)
    await session.commit()
    return site_svc.credential_out(row, settings)


@router.patch("/snmp-credentials/{cred_id}", response_model=SnmpCredentialOut, tags=["locais"])
async def update_credential(
    cred_id: uuid.UUID,
    body: SnmpCredentialUpdate,
    p: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
) -> SnmpCredentialOut:
    row = await site_svc.update_credential(session, settings, p, cred_id, body)
    await session.commit()
    return site_svc.credential_out(row, settings)


@router.delete("/snmp-credentials/{cred_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["locais"])
async def delete_credential(cred_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> Response:
    await site_svc.delete_credential(session, p, cred_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- devices (leitura)


@router.get("/devices", response_model=Page[DeviceOut], tags=["equipamentos"])
async def list_devices(
    p: PrincipalDep,
    session: SessionDep,
    site_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: str = "serial",
    direction: Direction = "asc",
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> Page[DeviceOut]:
    page = await devices_svc.list_devices(
        session,
        p,
        site_id=site_id,
        customer_id=customer_id,
        q=q,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )
    return Page(items=[DeviceOut.model_validate(d) for d in page.items], next_cursor=page.next_cursor)


@router.get(
    "/devices/{device_id}",
    response_model=DeviceDetail,
    tags=["equipamentos"],
    summary="Cadastro completo e atributos do equipamento",
)
async def get_device(device_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> DeviceDetail:
    return DeviceDetail.model_validate(await devices_svc.get_device(session, p, device_id))


@router.get(
    "/agents/{agent_id}/stats",
    response_model=AgentStats,
    tags=["coletores"],
    summary="Leituras, falhas e equipamentos sem resposta nas últimas 24 h",
)
async def agent_stats(agent_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> AgentStats:
    return await agents_svc.agent_stats(session, p, agent_id)


@router.get("/devices/{device_id}/readings", response_model=Page[ReadingOut], tags=["equipamentos"])
async def list_readings(
    device_id: uuid.UUID,
    p: PrincipalDep,
    session: SessionDep,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> Page[ReadingOut]:
    page = await devices_svc.list_readings(
        session, p, device_id, date_from=date_from, date_to=date_to, limit=limit, cursor=cursor
    )
    return Page(items=[ReadingOut.model_validate(r) for r in page.items], next_cursor=page.next_cursor)


@router.get("/devices/{device_id}/supplies", response_model=list[SupplyOut], tags=["equipamentos"])
async def list_supplies(device_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> list[SupplyOut]:
    return [SupplyOut.model_validate(s) for s in await devices_svc.list_supplies(session, p, device_id)]


@router.get("/devices/{device_id}/events", response_model=Page[DeviceEventOut], tags=["equipamentos"])
async def list_events(
    device_id: uuid.UUID,
    p: PrincipalDep,
    session: SessionDep,
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> Page[DeviceEventOut]:
    page = await devices_svc.list_events(session, p, device_id, limit=limit, cursor=cursor)
    return Page(items=[DeviceEventOut.model_validate(e) for e in page.items], next_cursor=page.next_cursor)
