"""/api/erp/v1: read-only API for the ERP, authenticated by an integration token (PROMPT 7).
The portal side (/api/v1/erp-tokens) creates and revokes the tokens."""

import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import PrincipalDep, SessionDep, SettingsDep
from app.core.errors import AppError
from app.core.ratelimit import RateLimiter
from app.schemas.common import ERROR_RESPONSES
from app.schemas.erp import (
    ErpCutoffResponse,
    ErpDevicePage,
    ErpReadingPage,
    ErpTokenCreated,
    ErpTokenIn,
    ErpTokenOut,
)
from app.schemas.erp_connector import (
    ErpConnectorSettings,
    ErpQueueCounts,
    ErpQueueDetail,
    ErpQueuePage,
    ErpRetryIn,
    ErpRetryOut,
)
from app.services import erp_api as svc
from app.services import erp_connector as connector_svc
from app.services.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

bearer = HTTPBearer(
    auto_error=False,
    scheme_name="ErpToken",
    description="Token de integração criado no portal (Configurações > Integração). Somente leitura.",
)

router = APIRouter(prefix="/api/erp/v1", responses=ERROR_RESPONSES, tags=["erp"])
tokens_router = APIRouter(responses=ERROR_RESPONSES, tags=["integração"])
CustomerCode = Annotated[
    str | None, Query(max_length=64, description="Código ERP do cliente (Clientes > Código ERP)")
]


async def erp_client(
    request: Request,
    session: SessionDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> svc.ErpClient:
    client = await svc.authenticate(session, creds.credentials if creds else None)
    limiter: RateLimiter = request.app.state.agent_limiter
    if not limiter.hit(f"erp:{client.token_id}"):
        raise AppError(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited", "Muitas requisições; aguarde")
    return client


ErpDep = Annotated[svc.ErpClient, Depends(erp_client)]


@router.get(
    "/readings",
    response_model=ErpReadingPage,
    summary="Leituras válidas do período",
    description=(
        "Leituras entre `from` e `to` (dias no horário de Brasília, inclusive), da mais antiga para a mais "
        "recente. Ajustes manuais já aplicados; leituras com regressão de contador não classificadas como "
        "válidas ficam de fora. Paginação por `cursor`."
    ),
)
async def readings(
    client: ErpDep,
    session: SessionDep,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    customer_erp_code: CustomerCode = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> ErpReadingPage:
    return await svc.readings(
        session,
        client,
        customer_erp_code=customer_erp_code,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )


@router.get(
    "/cutoff",
    response_model=ErpCutoffResponse,
    summary="Leitura de corte",
    description=(
        "Para cada equipamento, a leitura válida mais recente até o fim do dia `date` (horário de Brasília). "
        "Equipamento sem leitura até a data não aparece."
    ),
)
async def cutoff_readings(
    client: ErpDep,
    session: SessionDep,
    cutoff_date: Annotated[date, Query(alias="date")],
    customer_erp_code: CustomerCode = None,
) -> ErpCutoffResponse:
    return await svc.cutoff_readings(
        session, client, cutoff_date=cutoff_date, customer_erp_code=customer_erp_code
    )


@router.get("/devices", response_model=ErpDevicePage, summary="Equipamentos ativos no parque")
async def devices(
    client: ErpDep,
    session: SessionDep,
    customer_erp_code: CustomerCode = None,
    after: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> ErpDevicePage:
    return await svc.devices(session, client, customer_erp_code=customer_erp_code, after=after, limit=limit)


# ----------------------------------------------------------------------------- tokens (portal)


@tokens_router.get("/erp-tokens", response_model=list[ErpTokenOut], summary="Tokens de integração do ERP")
async def list_tokens(p: PrincipalDep, session: SessionDep) -> list[ErpTokenOut]:
    return await svc.list_tokens(session, p)


@tokens_router.post(
    "/erp-tokens",
    response_model=ErpTokenCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Criar token (o valor aparece só nesta resposta)",
)
async def create_token(body: ErpTokenIn, p: PrincipalDep, session: SessionDep) -> ErpTokenCreated:
    out = await svc.create_token(session, p, body.name)
    await session.commit()
    return out


@tokens_router.post("/erp-tokens/{token_id}/revoke", response_model=ErpTokenOut, summary="Revogar token")
async def revoke_token(token_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> ErpTokenOut:
    out = await svc.revoke_token(session, p, token_id)
    await session.commit()
    return out


# ----------------------------------------------------------------------------- conector Dataclassic (16.11)

connector_router = APIRouter(responses=ERROR_RESPONSES, tags=["integração"])


@connector_router.get("/erp-connector", response_model=ErpConnectorSettings, summary="Parâmetros do conector")
async def get_connector(p: PrincipalDep, session: SessionDep, settings: SettingsDep) -> ErpConnectorSettings:
    return await connector_svc.get_settings(session, settings, p)


@connector_router.put("/erp-connector", response_model=ErpConnectorSettings, summary="Salvar parâmetros")
async def put_connector(
    body: ErpConnectorSettings, p: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> ErpConnectorSettings:
    out = await connector_svc.put_settings(session, settings, p, body)
    await session.commit()
    return out


@connector_router.get(
    "/erp-queue", response_model=ErpQueuePage, summary="Fila do conector (recentes primeiro)"
)
async def list_queue(
    p: PrincipalDep,
    session: SessionDep,
    status: Literal["pending", "sent", "error"] | None = None,
    kind: Literal["counters", "supply_request", "service_order"] | None = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> ErpQueuePage:
    return await connector_svc.list_queue(session, p, status=status, kind=kind, cursor=cursor, limit=limit)


@connector_router.get("/erp-queue/counts", response_model=ErpQueueCounts, summary="Itens por status")
async def queue_counts(p: PrincipalDep, session: SessionDep) -> ErpQueueCounts:
    return await connector_svc.counts(session, p)


@connector_router.get(
    "/erp-queue/{item_id}", response_model=ErpQueueDetail, summary="Item com o conteúdo enviado"
)
async def queue_item(item_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> ErpQueueDetail:
    return await connector_svc.get_item(session, p, item_id)


@connector_router.post("/erp-queue/retry", response_model=ErpRetryOut, summary="Reenviar itens")
async def retry(body: ErpRetryIn, p: PrincipalDep, session: SessionDep) -> ErpRetryOut:
    n = await connector_svc.retry(session, p, body.ids)
    await session.commit()
    return ErpRetryOut(requeued=n)
