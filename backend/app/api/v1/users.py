"""/api/v1 users, roles and audit log."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import PrincipalDep, SessionDep, SettingsDep
from app.core.permissions import ROLE_PERMISSIONS
from app.models import AuditLog, User
from app.schemas.common import ERROR_RESPONSES, OkResponse, Page
from app.schemas.users import (
    AuditOut,
    ResetPasswordAdminRequest,
    ResetPasswordAdminResponse,
    RoleOut,
    UserCreated,
    UserIn,
    UserOut,
    UserUpdate,
)
from app.services import users as svc
from app.services.export import ExportColumn, ExportFormat, export_response
from app.services.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Direction

router = APIRouter(responses=ERROR_RESPONSES)

Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]

USER_COLUMNS: list[ExportColumn[User]] = [
    ExportColumn("Nome", lambda u: u.name),
    ExportColumn("E-mail", lambda u: u.email),
    ExportColumn("Papel", lambda u: u.role_code),
    ExportColumn("Ativo", lambda u: u.active),
    ExportColumn("Autenticador (TOTP)", lambda u: u.totp_enabled),
    ExportColumn("Último acesso", lambda u: u.last_login_at),
    ExportColumn("Criado em", lambda u: u.created_at),
]


@router.get(
    "/roles", response_model=list[RoleOut], tags=["usuários"], summary="Papéis que você pode atribuir"
)
async def list_roles(p: PrincipalDep) -> list[RoleOut]:
    return [
        RoleOut(code=code, name=name, level=level, permissions=sorted(ROLE_PERMISSIONS[code]))
        for code, name, level in svc.list_roles(p)
    ]


@router.get("/users", response_model=Page[UserOut], tags=["usuários"])
async def list_users(
    p: PrincipalDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    role: str | None = None,
    active: bool | None = None,
    customer_id: uuid.UUID | None = None,
    sort: str = "name",
    direction: Direction = "asc",
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> Page[UserOut]:
    page = await svc.list_users(
        session,
        p,
        q=q,
        role=role,
        active=active,
        customer_id=customer_id,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )
    return Page(items=[UserOut.model_validate(u) for u in page.items], next_cursor=page.next_cursor)


@router.get("/users/export", tags=["usuários"])
async def export_users(
    p: PrincipalDep,
    session: SessionDep,
    format: ExportFormat = "csv",  # noqa: A002
    q: Annotated[str | None, Query(max_length=200)] = None,
    role: str | None = None,
    active: bool | None = None,
    customer_id: uuid.UUID | None = None,
) -> Response:
    rows = await svc.export_users(session, p, q=q, role=role, active=active, customer_id=customer_id)
    return export_response(rows, USER_COLUMNS, fmt=format, basename="usuarios")


@router.post("/users", response_model=UserCreated, status_code=status.HTTP_201_CREATED, tags=["usuários"])
async def create_user(body: UserIn, p: PrincipalDep, session: SessionDep) -> UserCreated:
    obj, temporary = await svc.create_user(session, p, body)
    await session.commit()
    return UserCreated(user=UserOut.model_validate(obj), temporary_password=temporary)


@router.get("/users/{user_id}", response_model=UserOut, tags=["usuários"])
async def get_user(user_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> UserOut:
    return UserOut.model_validate(await svc.get_user(session, p, user_id))


@router.patch("/users/{user_id}", response_model=UserOut, tags=["usuários"])
async def update_user(user_id: uuid.UUID, body: UserUpdate, p: PrincipalDep, session: SessionDep) -> UserOut:
    obj = await svc.update_user(session, p, user_id, body)
    await session.commit()
    return UserOut.model_validate(obj)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["usuários"])
async def delete_user(user_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> Response:
    await svc.delete_user(session, p, user_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordAdminResponse, tags=["usuários"])
async def reset_password(
    user_id: uuid.UUID,
    body: ResetPasswordAdminRequest,
    p: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
) -> ResetPasswordAdminResponse:
    temporary = await svc.admin_reset_password(session, settings, p, user_id, mode=body.mode)
    await session.commit()
    return ResetPasswordAdminResponse(mode=body.mode, temporary_password=temporary)


@router.post("/users/{user_id}/reset-totp", response_model=OkResponse, tags=["usuários"])
async def reset_totp(user_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> OkResponse:
    await svc.admin_reset_totp(session, p, user_id)
    await session.commit()
    return OkResponse()


# ----------------------------------------------------------------------------- audit


AUDIT_COLUMNS: list[ExportColumn[tuple[AuditLog, str | None]]] = [
    ExportColumn("Data", lambda r: r[0].created_at),
    ExportColumn("Usuário", lambda r: r[1]),
    ExportColumn("Ação", lambda r: r[0].action),
    ExportColumn("Entidade", lambda r: r[0].entity),
    ExportColumn("ID", lambda r: r[0].entity_id),
    ExportColumn("Antes", lambda r: str(r[0].before) if r[0].before else ""),
    ExportColumn("Depois", lambda r: str(r[0].after) if r[0].after else ""),
    ExportColumn("IP", lambda r: r[0].ip),
]


@router.get("/audit", response_model=Page[AuditOut], tags=["auditoria"])
async def list_audit(
    p: PrincipalDep,
    session: SessionDep,
    user_id: uuid.UUID | None = None,
    entity: str | None = None,
    entity_id: str | None = None,
    action: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> Page[AuditOut]:
    page = await svc.list_audit(
        session,
        p,
        user_id=user_id,
        entity=entity,
        entity_id=entity_id,
        action=action,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        cursor=cursor,
    )
    emails = await svc.emails_for(session, {a.user_id for a in page.items if a.user_id})
    items = [
        AuditOut.model_validate(a).model_copy(
            update={"user_email": emails.get(a.user_id) if a.user_id else None}
        )
        for a in page.items
    ]
    return Page(items=items, next_cursor=page.next_cursor)


@router.get("/audit/export", tags=["auditoria"])
async def export_audit(
    p: PrincipalDep,
    session: SessionDep,
    format: ExportFormat = "csv",  # noqa: A002
    user_id: uuid.UUID | None = None,
    entity: str | None = None,
    action: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Response:
    p.require("audit.read")
    stmt = (
        svc.audit_query(
            p,
            user_id=user_id,
            entity=entity,
            entity_id=None,
            action=action,
            date_from=date_from,
            date_to=date_to,
        )
        .order_by(AuditLog.created_at.desc())
        .limit(100_000)
    )
    rows = list((await session.execute(stmt)).scalars())
    emails = await svc.emails_for(session, {a.user_id for a in rows if a.user_id})
    data = [(a, emails.get(a.user_id) if a.user_id else None) for a in rows]
    return export_response(data, AUDIT_COLUMNS, fmt=format, basename="auditoria")
