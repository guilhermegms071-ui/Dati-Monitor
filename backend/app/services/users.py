"""User management with role hierarchy and tenant scope; audit log listing."""

import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.permissions import CUSTOMER_VIEWER, ROLE_BY_CODE, SUPERADMIN, role_level
from app.core.principal import Principal, reseller_scope
from app.core.security import hash_password
from app.models import AuditLog, Customer, RefreshToken, Reseller, User
from app.schemas.users import UserIn, UserUpdate
from app.services import audit
from app.services.auth import send_reset_email
from app.services.pagination import Direction, PageResult, SortOption, paginate


def _now() -> datetime:
    return datetime.now(UTC)


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _check_role_assignable(p: Principal, role: str) -> None:
    if role == SUPERADMIN and not p.is_superadmin:
        raise forbidden("Somente o superadmin pode criar outro superadmin")
    if role_level(role) > p.level:
        raise forbidden("Você não pode atribuir um papel acima do seu")


def _check_can_manage(p: Principal, target: User) -> None:
    if not p.is_superadmin and role_level(target.role_code) > p.level:
        raise forbidden("Você não pode alterar um usuário com papel acima do seu")


async def _check_customer(
    session: AsyncSession, reseller_id: uuid.UUID, role: str, customer_id: uuid.UUID | None
) -> None:
    if role == CUSTOMER_VIEWER and customer_id is None:
        raise bad_request("customer_required", "Usuário de cliente precisa estar vinculado a um cliente")
    if role in (SUPERADMIN, "reseller_admin") and customer_id is not None:
        raise bad_request("customer_not_allowed", "Administradores não têm escopo de cliente")
    if customer_id is not None:
        customer = await session.get(Customer, customer_id)
        if customer is None or customer.deleted_at is not None or customer.reseller_id != reseller_id:
            raise not_found("Cliente")


async def _email_taken(session: AsyncSession, email: str, exclude: uuid.UUID | None = None) -> bool:
    stmt = select(func.count()).select_from(User).where(User.email == email, User.deleted_at.is_(None))
    if exclude:
        stmt = stmt.where(User.id != exclude)
    return (await session.execute(stmt)).scalar_one() > 0


USER_SORTS = {
    "name": SortOption(User.name, "str"),
    "email": SortOption(User.email, "str"),
    "created_at": SortOption(User.created_at, "datetime"),
    "last_login_at": SortOption(
        func.coalesce(User.last_login_at, datetime(1970, 1, 1, tzinfo=UTC)), "datetime"
    ),
}


def users_query(
    p: Principal, *, q: str | None, role: str | None, active: bool | None, customer_id: uuid.UUID | None
) -> Select[tuple[User]]:
    stmt = select(User).where(User.deleted_at.is_(None), reseller_scope(p, User.reseller_id))
    if role:
        stmt = stmt.where(User.role_code == role)
    if active is not None:
        stmt = stmt.where(User.active.is_(active))
    if customer_id:
        stmt = stmt.where(User.customer_id == customer_id)
    if q:
        stmt = stmt.where(or_(User.name.ilike(_like(q)), User.email.ilike(_like(q))))
    return stmt


async def list_users(
    session: AsyncSession,
    p: Principal,
    *,
    q: str | None,
    role: str | None,
    active: bool | None,
    customer_id: uuid.UUID | None,
    sort: str,
    direction: Direction,
    limit: int,
    cursor: str | None,
) -> PageResult[User]:
    p.require("users.read")
    return await paginate(
        session,
        users_query(p, q=q, role=role, active=active, customer_id=customer_id),
        id_column=User.id,
        sort_options=USER_SORTS,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )


async def export_users(
    session: AsyncSession,
    p: Principal,
    *,
    q: str | None,
    role: str | None,
    active: bool | None,
    customer_id: uuid.UUID | None,
) -> list[User]:
    p.require("users.read")
    stmt = users_query(p, q=q, role=role, active=active, customer_id=customer_id).order_by(User.name)
    return list((await session.execute(stmt)).scalars())


async def get_user(session: AsyncSession, p: Principal, user_id: uuid.UUID) -> User:
    p.require("users.read")
    obj = await session.get(User, user_id)
    if obj is None or obj.deleted_at is not None or not p.can_access_reseller(obj.reseller_id):
        raise not_found("Usuário")
    return obj


async def create_user(session: AsyncSession, p: Principal, data: UserIn) -> tuple[User, str | None]:
    p.require("users.write")
    reseller_id = data.reseller_id or p.reseller_id
    if reseller_id != p.reseller_id and not p.is_superadmin:
        raise forbidden("Somente o superadmin cria usuários em outra revenda")
    reseller = await session.get(Reseller, reseller_id)
    if reseller is None or reseller.deleted_at is not None:
        raise not_found("Revenda")
    _check_role_assignable(p, data.role)
    await _check_customer(session, reseller_id, data.role, data.customer_id)
    if await _email_taken(session, data.email):
        raise conflict("email_taken", "Já existe um usuário com este e-mail")
    temporary = None
    password = data.password
    if password is None:
        temporary = secrets.token_urlsafe(12)
        password = temporary
    obj = User(
        reseller_id=reseller_id,
        customer_id=data.customer_id,
        name=data.name,
        email=data.email,
        password_hash=hash_password(password),
        role_code=data.role,
        active=data.active,
        # Senha definida por outra pessoa: o usuário troca no primeiro acesso.
        must_change_password=True,
        password_changed_at=_now(),
    )
    session.add(obj)
    await session.flush()
    await audit.record(
        session,
        p,
        action="create",
        entity="user",
        entity_id=obj.id,
        reseller_id=reseller_id,
        after=audit.snapshot(obj),
    )
    return obj, temporary


async def update_user(session: AsyncSession, p: Principal, user_id: uuid.UUID, data: UserUpdate) -> User:
    p.require("users.write")
    obj = await get_user(session, p, user_id)
    _check_can_manage(p, obj)
    before = audit.snapshot(obj)
    changes = data.model_dump(exclude_unset=True)
    is_self = obj.id == p.user_id
    if is_self and (
        changes.get("active") is False or ("role" in changes and changes["role"] != obj.role_code)
    ):
        raise bad_request("cannot_change_self", "Você não pode desativar nem trocar o próprio papel")
    role = changes.get("role") or obj.role_code
    if "role" in changes and changes["role"] is not None:
        _check_role_assignable(p, changes["role"])
    customer_id = obj.customer_id
    if data.clear_customer:
        customer_id = None
    elif changes.get("customer_id") is not None:
        customer_id = changes["customer_id"]
    await _check_customer(session, obj.reseller_id, role, customer_id)
    if (
        changes.get("email")
        and changes["email"] != obj.email
        and await _email_taken(session, changes["email"], obj.id)
    ):
        raise conflict("email_taken", "Já existe um usuário com este e-mail")
    if changes.get("name"):
        obj.name = changes["name"]
    if changes.get("email"):
        obj.email = changes["email"]
    obj.role_code = role
    obj.customer_id = customer_id
    if changes.get("active") is not None:
        obj.active = changes["active"]
        if not obj.active:
            await _revoke_sessions(session, obj.id)
    if data.unlock:
        obj.locked_until = None
        obj.failed_login_count = 0
    await session.flush()
    b, a = audit.diff(before, audit.snapshot(obj))
    await audit.record(
        session,
        p,
        action="update",
        entity="user",
        entity_id=obj.id,
        reseller_id=obj.reseller_id,
        before=b,
        after=a,
    )
    return obj


async def _revoke_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def delete_user(session: AsyncSession, p: Principal, user_id: uuid.UUID) -> None:
    p.require("users.write")
    obj = await get_user(session, p, user_id)
    _check_can_manage(p, obj)
    if obj.id == p.user_id:
        raise bad_request("cannot_delete_self", "Você não pode excluir o próprio usuário")
    obj.deleted_at = _now()
    obj.active = False
    await _revoke_sessions(session, obj.id)
    await audit.record(
        session, p, action="delete", entity="user", entity_id=obj.id, reseller_id=obj.reseller_id
    )


async def admin_reset_password(
    session: AsyncSession, settings: Settings, p: Principal, user_id: uuid.UUID, *, mode: str
) -> str | None:
    p.require("users.write")
    obj = await get_user(session, p, user_id)
    _check_can_manage(p, obj)
    temporary: str | None = None
    if mode == "temporary":
        temporary = secrets.token_urlsafe(12)
        obj.password_hash = hash_password(temporary)
        obj.password_changed_at = _now()
        obj.must_change_password = True
        obj.failed_login_count = 0
        obj.locked_until = None
        await _revoke_sessions(session, obj.id)
    else:
        await send_reset_email(session, settings, obj)
    await audit.record(
        session,
        p,
        action="reset_password",
        entity="user",
        entity_id=obj.id,
        reseller_id=obj.reseller_id,
        after={"mode": mode},
    )
    return temporary


async def admin_reset_totp(session: AsyncSession, p: Principal, user_id: uuid.UUID) -> None:
    p.require("users.write")
    obj = await get_user(session, p, user_id)
    _check_can_manage(p, obj)
    obj.totp_enabled = False
    obj.totp_secret_enc = None
    obj.totp_last_step = None
    await audit.record(
        session, p, action="reset_totp", entity="user", entity_id=obj.id, reseller_id=obj.reseller_id
    )


def list_roles(p: Principal) -> list[tuple[str, str, int]]:
    """Roles the principal may assign (for the user form)."""
    return [
        (r.code, r.name, r.level)
        for r in ROLE_BY_CODE.values()
        if (r.code != SUPERADMIN or p.is_superadmin) and r.level <= p.level
    ]


# ----------------------------------------------------------------------------- audit


AUDIT_SORTS = {"created_at": SortOption(AuditLog.created_at, "datetime")}


def audit_query(
    p: Principal,
    *,
    user_id: uuid.UUID | None,
    entity: str | None,
    entity_id: str | None,
    action: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> Select[tuple[AuditLog]]:
    stmt = select(AuditLog)
    if not p.is_superadmin:
        stmt = stmt.where(AuditLog.reseller_id == p.reseller_id)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if entity:
        stmt = stmt.where(AuditLog.entity == entity)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if date_from:
        stmt = stmt.where(AuditLog.created_at >= date_from)
    if date_to:
        stmt = stmt.where(AuditLog.created_at < date_to)
    return stmt


async def list_audit(
    session: AsyncSession,
    p: Principal,
    *,
    user_id: uuid.UUID | None,
    entity: str | None,
    entity_id: str | None,
    action: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
    cursor: str | None,
) -> PageResult[AuditLog]:
    p.require("audit.read")
    return await paginate(
        session,
        audit_query(
            p,
            user_id=user_id,
            entity=entity,
            entity_id=entity_id,
            action=action,
            date_from=date_from,
            date_to=date_to,
        ),
        id_column=AuditLog.id,
        sort_options=AUDIT_SORTS,
        sort="created_at",
        direction="desc",
        limit=limit,
        cursor=cursor,
    )


async def emails_for(session: AsyncSession, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not user_ids:
        return {}
    rows = await session.execute(select(User.id, User.email).where(User.id.in_(user_ids)))
    return {row.id: row.email for row in rows}
