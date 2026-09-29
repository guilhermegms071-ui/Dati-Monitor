"""Permission matrix per reseller (PROMPT 16.14): effective permissions of a user and the editable matrix.

Special permissions always follow the role; the matrix permissions of the operational roles can be
adjusted by the reseller (a row in `reseller_role_permissions` replaces the default matrix of that role).
"""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, forbidden
from app.core.permissions import (
    ACTIONS,
    EDITABLE_ROLES,
    MATRIX_PERMISSIONS,
    MODULES,
    ROLE_BY_CODE,
    ROLE_PERMISSIONS,
    SUPPLIES_MONITOR,
    grantable,
)
from app.core.principal import Principal
from app.models import ResellerRolePermission
from app.schemas.permissions import MatrixModule, PermissionMatrix, RoleMatrix
from app.services import audit


def combine(role_code: str, custom: list[str] | None) -> frozenset[str]:
    default = ROLE_PERMISSIONS[role_code]
    if custom is None or role_code not in EDITABLE_ROLES:
        return default
    special = default - MATRIX_PERMISSIONS
    return frozenset(special | (set(custom) & grantable(role_code)))


async def effective_permissions(
    session: AsyncSession, reseller_id: uuid.UUID, role_code: str
) -> frozenset[str]:
    if role_code not in EDITABLE_ROLES:
        return ROLE_PERMISSIONS[role_code]
    custom = (
        await session.execute(
            select(ResellerRolePermission.permissions).where(
                ResellerRolePermission.reseller_id == reseller_id,
                ResellerRolePermission.role_code == role_code,
            )
        )
    ).scalar_one_or_none()
    return combine(role_code, [str(x) for x in custom] if custom is not None else None)


def _modules() -> list[MatrixModule]:
    return [MatrixModule(code=code, name=name) for code, name in MODULES.items()]


async def get_matrix(session: AsyncSession, p: Principal, reseller_id: uuid.UUID | None) -> PermissionMatrix:
    p.require("users.read")
    target = _target_reseller(p, reseller_id)
    rows = {
        r.role_code: r
        for r in (
            await session.execute(
                select(ResellerRolePermission).where(ResellerRolePermission.reseller_id == target)
            )
        ).scalars()
    }
    roles: list[RoleMatrix] = []
    for code in EDITABLE_ROLES:
        row = rows.get(code)
        perms = combine(code, [str(x) for x in row.permissions] if row else None)
        roles.append(
            RoleMatrix(
                role=code,
                role_name=ROLE_BY_CODE[code].name,
                permissions=sorted(perms & MATRIX_PERMISSIONS),
                grantable=sorted(grantable(code)),
                customized=row is not None,
            )
        )
    return PermissionMatrix(
        reseller_id=target,
        modules=_modules(),
        actions=dict(ACTIONS),
        supplies_permission=SUPPLIES_MONITOR,
        roles=roles,
    )


def _target_reseller(p: Principal, reseller_id: uuid.UUID | None) -> uuid.UUID:
    if reseller_id is None or reseller_id == p.reseller_id:
        return p.reseller_id
    if not p.is_superadmin:
        raise forbidden("A matriz de outra revenda só pode ser vista pelo superadministrador")
    return reseller_id


async def set_role_matrix(
    session: AsyncSession,
    p: Principal,
    *,
    reseller_id: uuid.UUID | None,
    role_code: str,
    permissions: list[str] | None,
) -> PermissionMatrix:
    """Replaces the matrix of one role (`None` restores the system default)."""
    p.require("permissions.write")
    target = _target_reseller(p, reseller_id)
    if role_code not in EDITABLE_ROLES:
        raise bad_request("role_not_editable", "A matriz deste papel não pode ser alterada")
    before = await effective_permissions(session, target, role_code)
    if permissions is None:
        row = await session.get(ResellerRolePermission, (target, role_code))
        if row is not None:
            await session.delete(row)
    else:
        invalid = sorted(set(permissions) - grantable(role_code))
        if invalid:
            raise bad_request(
                "permission_not_grantable",
                f"Permissões que este papel não pode receber: {', '.join(invalid)}",
                permissions=invalid,
            )
        await session.execute(
            insert(ResellerRolePermission)
            .values(
                reseller_id=target,
                role_code=role_code,
                permissions=sorted(set(permissions)),
                updated_by=p.user_id,
            )
            .on_conflict_do_update(
                index_elements=[ResellerRolePermission.reseller_id, ResellerRolePermission.role_code],
                set_={"permissions": sorted(set(permissions)), "updated_by": p.user_id},
            )
        )
    await session.flush()
    after = await effective_permissions(session, target, role_code)
    await audit.record(
        session,
        p,
        action="update",
        entity="role_permissions",
        entity_id=None,
        reseller_id=target,
        before={"role": role_code, "permissions": sorted(before & MATRIX_PERMISSIONS)},
        after={
            "role": role_code,
            "permissions": sorted(after & MATRIX_PERMISSIONS),
            "default": permissions is None,
        },
    )
    return await get_matrix(session, p, target)
