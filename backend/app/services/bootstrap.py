"""Startup bootstrap: role/permission sync, first superadmin and development seed data."""

import logging
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.permissions import ROLE_PERMISSIONS, ROLES, SUPERADMIN
from app.core.security import hash_password
from app.models import Company, Customer, Reseller, Role, RolePermission, Site, User
from app.services.site_config import add_default_credential

logger = logging.getLogger(__name__)

# Lock consultivo para que duas instâncias da API não façam o bootstrap ao mesmo tempo.
_BOOTSTRAP_LOCK_KEY = 0x44_4D_42_4F  # "DMBO"


async def sync_roles(session: AsyncSession) -> None:
    """Mirrors app.core.permissions into the roles and role_permissions tables."""
    for role in ROLES:
        await session.execute(
            insert(Role)
            .values(code=role.code, name=role.name, level=role.level)
            .on_conflict_do_update(
                index_elements=[Role.code],
                set_={"name": role.name, "level": role.level, "updated_at": func.now()},
            )
        )
    for code, perms in ROLE_PERMISSIONS.items():
        await session.execute(
            delete(RolePermission).where(
                RolePermission.role_code == code, RolePermission.permission.not_in(perms)
            )
        )
        for perm in sorted(perms):
            await session.execute(
                insert(RolePermission).values(role_code=code, permission=perm).on_conflict_do_nothing()
            )


@dataclass(frozen=True)
class BootstrapResult:
    created_admin: bool
    admin_email: str | None = None
    admin_password: str | None = None


async def ensure_bootstrap(session: AsyncSession, settings: Settings) -> BootstrapResult:
    """Creates the first reseller and superadmin when the database has no users (first start)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _BOOTSTRAP_LOCK_KEY})
    await sync_roles(session)
    if (await session.execute(select(func.count()).select_from(User))).scalar_one() > 0:
        return BootstrapResult(created_admin=False)
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
        reseller = Reseller(name=settings.bootstrap_reseller_name)
        session.add(reseller)
        await session.flush()
    password = secrets.token_urlsafe(12)
    session.add(
        User(
            reseller_id=reseller.id,
            name="Administrador",
            email=settings.bootstrap_admin_email.lower(),
            password_hash=hash_password(password),
            role_code=SUPERADMIN,
            must_change_password=True,
            password_changed_at=datetime.now(UTC),
        )
    )
    return BootstrapResult(
        created_admin=True, admin_email=settings.bootstrap_admin_email, admin_password=password
    )


def announce_bootstrap(result: BootstrapResult) -> None:
    """Shows the one-time admin password on the console (PROMPT section 7: shown on first start).

    Printed to stderr outside the structured log; it must be changed on first login.
    """
    if not result.created_admin:
        return
    banner = (
        "\n" + "=" * 72 + "\n"
        "  PRIMEIRO ACESSO — usuário administrador criado\n"
        f"  E-mail: {result.admin_email}\n"
        f"  Senha temporária: {result.admin_password}\n"
        "  A troca da senha é obrigatória no primeiro login.\n" + "=" * 72 + "\n"
    )
    sys.stderr.write(banner)
    sys.stderr.flush()
    logger.warning("usuário administrador inicial criado (%s); senha exibida no console", result.admin_email)


@dataclass(frozen=True)
class SeedResult:
    created: bool
    reseller_id: str


async def seed_dev(session: AsyncSession, settings: Settings) -> SeedResult:
    """Development seed (PROMPT section 7): Daticopy reseller, 1 company, 2 customers, 2 sites. Idempotent."""
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
        raise RuntimeError(
            "Nenhuma revenda: rode o bootstrap (inicie a API ou `python -m app.cli bootstrap`) antes"
        )
    existing = (
        await session.execute(
            select(func.count()).select_from(Company).where(Company.reseller_id == reseller.id)
        )
    ).scalar_one()
    if existing:
        return SeedResult(created=False, reseller_id=str(reseller.id))
    company = Company(reseller_id=reseller.id, legal_name="Daticopy Locação de Equipamentos Ltda")
    session.add(company)
    await session.flush()
    customers = [
        Customer(
            reseller_id=reseller.id,
            company_id=company.id,
            name="Cliente Exemplo Centro",
            contact_name="Contato Centro",
            phone="(21) 3333-0001",
            email="contato.centro@example.com",
            erp_code="CLI-0001",
        ),
        Customer(
            reseller_id=reseller.id,
            company_id=company.id,
            name="Cliente Exemplo Barra",
            contact_name="Contato Barra",
            phone="(21) 3333-0002",
            email="contato.barra@example.com",
            erp_code="CLI-0002",
        ),
    ]
    session.add_all(customers)
    await session.flush()
    sites = [
        Site(
            reseller_id=reseller.id,
            customer_id=customers[0].id,
            name="Matriz",
            district="Centro",
            city="Rio de Janeiro",
            state="RJ",
        ),
        Site(
            reseller_id=reseller.id,
            customer_id=customers[1].id,
            name="Filial",
            district="Barra da Tijuca",
            city="Rio de Janeiro",
            state="RJ",
        ),
    ]
    session.add_all(sites)
    await session.flush()
    for site in sites:
        await add_default_credential(session, settings, site)
    return SeedResult(created=True, reseller_id=str(reseller.id))
