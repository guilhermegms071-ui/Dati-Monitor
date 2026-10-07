"""/api/v1 Descobertas (PROMPT 16.1), custom fields (16.7) and the permission matrix (16.14)."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status

from app.api.deps import PrincipalDep, SessionDep
from app.schemas.common import ERROR_RESPONSES
from app.schemas.custom_fields import CustomFieldIn, CustomFieldOut, CustomFieldUpdate
from app.schemas.discoveries import (
    DecisionIn,
    DecisionOut,
    DiscoveryCounts,
    DiscoveryPage,
    TransferDecisionIn,
    TransferPage,
)
from app.schemas.permissions import PermissionMatrix, RoleMatrixIn
from app.services import custom_fields as custom_fields_svc
from app.services import discoveries as svc
from app.services import park as park_svc
from app.services import permissions as permissions_svc
from app.services import transfers as transfers_svc
from app.services.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Direction

router = APIRouter(responses=ERROR_RESPONSES)
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


@router.get(
    "/discoveries",
    response_model=DiscoveryPage,
    tags=["equipamentos"],
    summary="Equipamentos descobertos aguardando decisão (ou descartados)",
)
async def list_discoveries(
    p: PrincipalDep,
    session: SessionDep,
    state: Literal["pending", "discarded"] = "pending",
    q: str | None = None,
    customer_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    sort: str = "first_seen_at",
    direction: Direction = "desc",
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> DiscoveryPage:
    page, total = await svc.list_discoveries(
        session,
        p,
        state=state,
        q=q,
        customer_id=customer_id,
        site_id=site_id,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )
    return DiscoveryPage(
        items=await park_svc.enrich(session, page.items), next_cursor=page.next_cursor, total=total
    )


@router.get("/discoveries/counts", response_model=DiscoveryCounts, tags=["equipamentos"])
async def discovery_counts(p: PrincipalDep, session: SessionDep) -> DiscoveryCounts:
    return await svc.counts(session, p)


@router.post(
    "/discoveries/decide",
    response_model=DecisionOut,
    tags=["equipamentos"],
    summary="Ativar, descartar ou restaurar (individual ou em lote)",
)
async def decide(body: DecisionIn, p: PrincipalDep, session: SessionDep) -> DecisionOut:
    out = await svc.decide(session, p, body)
    await session.commit()
    return out


@router.get(
    "/transfers",
    response_model=TransferPage,
    tags=["equipamentos"],
    summary="Equipamentos que apareceram num local de outro cliente (aguardando decisão)",
)
async def list_transfers(
    p: PrincipalDep,
    session: SessionDep,
    direction: Direction = "desc",
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> TransferPage:
    return await transfers_svc.list_pending(session, p, direction=direction, limit=limit, cursor=cursor)


@router.post(
    "/devices/{device_id}/transfer",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["equipamentos"],
    summary="Aprovar (passa para o novo cliente) ou recusar (mantém no atual) a transferência",
)
async def decide_transfer(
    device_id: uuid.UUID, body: TransferDecisionIn, p: PrincipalDep, session: SessionDep
) -> Response:
    await transfers_svc.decide(session, p, device_id, body)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- campos personalizados


@router.get("/custom-fields", response_model=list[CustomFieldOut], tags=["configurações"])
async def list_custom_fields(p: PrincipalDep, session: SessionDep) -> list[CustomFieldOut]:
    return [CustomFieldOut.model_validate(r) for r in await custom_fields_svc.list_for(session, p)]


@router.post(
    "/custom-fields",
    response_model=CustomFieldOut,
    status_code=status.HTTP_201_CREATED,
    tags=["configurações"],
)
async def create_custom_field(body: CustomFieldIn, p: PrincipalDep, session: SessionDep) -> CustomFieldOut:
    row = await custom_fields_svc.create(session, p, body)
    await session.commit()
    return CustomFieldOut.model_validate(row)


@router.patch("/custom-fields/{field_id}", response_model=CustomFieldOut, tags=["configurações"])
async def update_custom_field(
    field_id: uuid.UUID, body: CustomFieldUpdate, p: PrincipalDep, session: SessionDep
) -> CustomFieldOut:
    row = await custom_fields_svc.update(session, p, field_id, body)
    await session.commit()
    return CustomFieldOut.model_validate(row)


@router.delete("/custom-fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["configurações"])
async def delete_custom_field(field_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> Response:
    await custom_fields_svc.delete(session, p, field_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- matriz de permissões


@router.get("/permissions/matrix", response_model=PermissionMatrix, tags=["usuários"])
async def permission_matrix(
    p: PrincipalDep, session: SessionDep, reseller_id: uuid.UUID | None = None
) -> PermissionMatrix:
    return await permissions_svc.get_matrix(session, p, reseller_id)


@router.put(
    "/permissions/matrix/{role}",
    response_model=PermissionMatrix,
    tags=["usuários"],
    summary="Ajustar a matriz de um papel (null volta ao padrão)",
)
async def set_role_matrix(
    role: str, body: RoleMatrixIn, p: PrincipalDep, session: SessionDep, reseller_id: uuid.UUID | None = None
) -> PermissionMatrix:
    out = await permissions_svc.set_role_matrix(
        session, p, reseller_id=reseller_id, role_code=role, permissions=body.permissions
    )
    await session.commit()
    return out
