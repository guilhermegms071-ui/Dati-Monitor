"""Resellers, companies, customers and sites — always filtered by the principal's scope."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.principal import Principal, customer_scope, reseller_scope
from app.models import Agent, Company, Customer, Device, Reseller, Site, User
from app.schemas.tenancy import (
    CompanyIn,
    CompanyUpdate,
    CustomerIn,
    CustomerUpdate,
    ResellerIn,
    ResellerUpdate,
    SiteIn,
    SiteUpdate,
)
from app.services import audit
from app.services.agents import bump_site_config
from app.services.pagination import Direction, PageResult, SortOption, paginate
from app.services.site_config import add_default_credential


def _now() -> datetime:
    return datetime.now(UTC)


def _like(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_REQUIRED = frozenset({"name", "legal_name", "timezone", "collection_config", "company_id", "active"})


def _apply(obj: object, data: dict[str, Any]) -> None:
    for key, value in data.items():
        if value is None and key in _REQUIRED:
            raise bad_request("field_required", f"O campo {key} não pode ser vazio", field=key)
        setattr(obj, key, value)


# ----------------------------------------------------------------------------- resellers


RESELLER_SORTS = {
    "name": SortOption(Reseller.name, "str"),
    "created_at": SortOption(Reseller.created_at, "datetime"),
}


async def list_resellers(
    session: AsyncSession,
    p: Principal,
    *,
    q: str | None,
    sort: str,
    direction: Direction,
    limit: int,
    cursor: str | None,
) -> PageResult[Reseller]:
    p.require("resellers.read")
    stmt = select(Reseller).where(Reseller.deleted_at.is_(None), reseller_scope(p, Reseller.id))
    if q:
        stmt = stmt.where(Reseller.name.ilike(_like(q)))
    return await paginate(
        session,
        stmt,
        id_column=Reseller.id,
        sort_options=RESELLER_SORTS,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )


async def get_reseller(session: AsyncSession, p: Principal, reseller_id: uuid.UUID) -> Reseller:
    p.require("resellers.read")
    obj = await session.get(Reseller, reseller_id)
    if obj is None or obj.deleted_at is not None or not p.can_access_reseller(obj.id):
        raise not_found("Revenda")
    return obj


async def create_reseller(session: AsyncSession, p: Principal, data: ResellerIn) -> Reseller:
    p.require("resellers.write")
    obj = Reseller(**data.model_dump())
    session.add(obj)
    await session.flush()
    await audit.record(
        session,
        p,
        action="create",
        entity="reseller",
        entity_id=obj.id,
        reseller_id=obj.id,
        after=audit.snapshot(obj),
    )
    return obj


async def update_reseller(
    session: AsyncSession, p: Principal, reseller_id: uuid.UUID, data: ResellerUpdate
) -> Reseller:
    p.require("resellers.write")
    obj = await get_reseller(session, p, reseller_id)
    before = audit.snapshot(obj)
    _apply(obj, data.model_dump(exclude_unset=True))
    await session.flush()
    b, a = audit.diff(before, audit.snapshot(obj))
    await audit.record(
        session,
        p,
        action="update",
        entity="reseller",
        entity_id=obj.id,
        reseller_id=obj.id,
        before=b,
        after=a,
    )
    return obj


async def delete_reseller(session: AsyncSession, p: Principal, reseller_id: uuid.UUID) -> None:
    p.require("resellers.write")
    obj = await get_reseller(session, p, reseller_id)
    if obj.id == p.reseller_id:
        raise bad_request("cannot_delete_own_reseller", "Não é possível excluir a própria revenda")
    active_companies = (
        await session.execute(
            select(func.count())
            .select_from(Company)
            .where(Company.reseller_id == obj.id, Company.deleted_at.is_(None))
        )
    ).scalar_one()
    if active_companies:
        raise conflict("reseller_has_companies", "A revenda ainda tem empresas cadastradas")
    obj.deleted_at = _now()
    await audit.record(session, p, action="delete", entity="reseller", entity_id=obj.id, reseller_id=obj.id)


# ----------------------------------------------------------------------------- companies


COMPANY_SORTS = {
    "legal_name": SortOption(Company.legal_name, "str"),
    "created_at": SortOption(Company.created_at, "datetime"),
}


def _target_reseller(p: Principal, requested: uuid.UUID | None) -> uuid.UUID:
    if requested is None or requested == p.reseller_id:
        return p.reseller_id
    if not p.is_superadmin:
        raise forbidden("Somente o superadmin cria registros em outra revenda")
    return requested


async def list_companies(
    session: AsyncSession,
    p: Principal,
    *,
    q: str | None,
    reseller_id: uuid.UUID | None,
    sort: str,
    direction: Direction,
    limit: int,
    cursor: str | None,
) -> PageResult[Company]:
    p.require("companies.read")
    stmt = select(Company).where(Company.deleted_at.is_(None), reseller_scope(p, Company.reseller_id))
    if p.customer_id is not None:
        stmt = stmt.where(Company.id.in_(select(Customer.company_id).where(Customer.id == p.customer_id)))
    if reseller_id:
        stmt = stmt.where(Company.reseller_id == reseller_id)
    if q:
        stmt = stmt.where(or_(Company.legal_name.ilike(_like(q)), Company.cnpj.ilike(_like(q))))
    return await paginate(
        session,
        stmt,
        id_column=Company.id,
        sort_options=COMPANY_SORTS,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )


async def get_company(session: AsyncSession, p: Principal, company_id: uuid.UUID) -> Company:
    p.require("companies.read")
    obj = await session.get(Company, company_id)
    if obj is None or obj.deleted_at is not None or not p.can_access_reseller(obj.reseller_id):
        raise not_found("Empresa")
    if p.customer_id is not None:
        owner = await session.get(Customer, p.customer_id)
        if owner is None or owner.company_id != obj.id:
            raise not_found("Empresa")
    return obj


async def create_company(session: AsyncSession, p: Principal, data: CompanyIn) -> Company:
    p.require("companies.write")
    reseller_id = _target_reseller(p, data.reseller_id)
    if await session.get(Reseller, reseller_id) is None:
        raise not_found("Revenda")
    obj = Company(reseller_id=reseller_id, legal_name=data.legal_name, cnpj=data.cnpj)
    session.add(obj)
    await session.flush()
    await audit.record(
        session,
        p,
        action="create",
        entity="company",
        entity_id=obj.id,
        reseller_id=reseller_id,
        after=audit.snapshot(obj),
    )
    return obj


async def update_company(
    session: AsyncSession, p: Principal, company_id: uuid.UUID, data: CompanyUpdate
) -> Company:
    p.require("companies.write")
    obj = await get_company(session, p, company_id)
    before = audit.snapshot(obj)
    _apply(obj, data.model_dump(exclude_unset=True))
    await session.flush()
    b, a = audit.diff(before, audit.snapshot(obj))
    await audit.record(
        session,
        p,
        action="update",
        entity="company",
        entity_id=obj.id,
        reseller_id=obj.reseller_id,
        before=b,
        after=a,
    )
    return obj


async def delete_company(session: AsyncSession, p: Principal, company_id: uuid.UUID) -> None:
    p.require("companies.write")
    obj = await get_company(session, p, company_id)
    customers = (
        await session.execute(
            select(func.count())
            .select_from(Customer)
            .where(Customer.company_id == obj.id, Customer.deleted_at.is_(None))
        )
    ).scalar_one()
    if customers:
        raise conflict("company_has_customers", "A empresa ainda tem clientes cadastrados")
    obj.deleted_at = _now()
    await audit.record(
        session, p, action="delete", entity="company", entity_id=obj.id, reseller_id=obj.reseller_id
    )


# ----------------------------------------------------------------------------- customers


CUSTOMER_SORTS = {
    "name": SortOption(Customer.name, "str"),
    "erp_code": SortOption(func.coalesce(Customer.erp_code, ""), "str"),
    "created_at": SortOption(Customer.created_at, "datetime"),
}


def customers_query(
    p: Principal, *, q: str | None, company_id: uuid.UUID | None, active: bool | None
) -> Select[tuple[Customer]]:
    stmt = select(Customer).where(
        Customer.deleted_at.is_(None), reseller_scope(p, Customer.reseller_id), customer_scope(p, Customer.id)
    )
    if company_id:
        stmt = stmt.where(Customer.company_id == company_id)
    if active is not None:
        stmt = stmt.where(Customer.active.is_(active))
    if q:
        like = _like(q)
        stmt = stmt.where(
            or_(Customer.name.ilike(like), Customer.erp_code.ilike(like), Customer.cnpj.ilike(like))
        )
    return stmt


async def list_customers(
    session: AsyncSession,
    p: Principal,
    *,
    q: str | None,
    company_id: uuid.UUID | None,
    active: bool | None,
    sort: str,
    direction: Direction,
    limit: int,
    cursor: str | None,
) -> PageResult[Customer]:
    p.require("customers.read")
    return await paginate(
        session,
        customers_query(p, q=q, company_id=company_id, active=active),
        id_column=Customer.id,
        sort_options=CUSTOMER_SORTS,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )


async def export_customers(
    session: AsyncSession, p: Principal, *, q: str | None, company_id: uuid.UUID | None, active: bool | None
) -> list[Customer]:
    p.require("customers.read")
    stmt = customers_query(p, q=q, company_id=company_id, active=active).order_by(Customer.name)
    return list((await session.execute(stmt)).scalars())


async def get_customer(session: AsyncSession, p: Principal, customer_id: uuid.UUID) -> Customer:
    p.require("customers.read")
    obj = await session.get(Customer, customer_id)
    if obj is None or obj.deleted_at is not None or not p.can_access_customer(obj.reseller_id, obj.id):
        raise not_found("Cliente")
    return obj


async def _company_in_scope(session: AsyncSession, p: Principal, company_id: uuid.UUID) -> Company:
    company = await session.get(Company, company_id)
    if company is None or company.deleted_at is not None or not p.can_access_reseller(company.reseller_id):
        raise not_found("Empresa")
    return company


async def _flush_unique(session: AsyncSession) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        if "uq_customers_reseller_erp_code" in str(exc.orig):
            raise conflict("erp_code_taken", "Já existe um cliente com este código do ERP") from exc
        raise


async def create_customer(session: AsyncSession, p: Principal, data: CustomerIn) -> Customer:
    p.require("customers.write")
    if p.customer_id is not None:
        raise forbidden("Usuários com escopo de cliente não criam clientes")
    company = await _company_in_scope(session, p, data.company_id)
    obj = Customer(reseller_id=company.reseller_id, **data.model_dump())
    session.add(obj)
    await _flush_unique(session)
    await audit.record(
        session,
        p,
        action="create",
        entity="customer",
        entity_id=obj.id,
        reseller_id=obj.reseller_id,
        after=audit.snapshot(obj),
    )
    return obj


async def update_customer(
    session: AsyncSession, p: Principal, customer_id: uuid.UUID, data: CustomerUpdate
) -> Customer:
    p.require("customers.write")
    obj = await get_customer(session, p, customer_id)
    before = audit.snapshot(obj)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("company_id") is not None:
        company = await _company_in_scope(session, p, changes["company_id"])
        if company.reseller_id != obj.reseller_id:
            raise bad_request("company_other_reseller", "A empresa pertence a outra revenda")
    _apply(obj, changes)
    await _flush_unique(session)
    b, a = audit.diff(before, audit.snapshot(obj))
    await audit.record(
        session,
        p,
        action="update",
        entity="customer",
        entity_id=obj.id,
        reseller_id=obj.reseller_id,
        before=b,
        after=a,
    )
    return obj


async def delete_customer(session: AsyncSession, p: Principal, customer_id: uuid.UUID) -> None:
    p.require("customers.write")
    obj = await get_customer(session, p, customer_id)
    for model, label in ((Site, "locais"), (Device, "equipamentos")):
        count = (
            await session.execute(
                select(func.count())
                .select_from(model)
                .where(model.customer_id == obj.id, model.deleted_at.is_(None))
            )
        ).scalar_one()
        if count:
            raise conflict("customer_in_use", f"O cliente ainda tem {label} cadastrados")
    users = (
        await session.execute(
            select(func.count())
            .select_from(User)
            .where(User.customer_id == obj.id, User.deleted_at.is_(None))
        )
    ).scalar_one()
    if users:
        raise conflict("customer_in_use", "O cliente ainda tem usuários vinculados")
    obj.deleted_at = _now()
    await audit.record(
        session, p, action="delete", entity="customer", entity_id=obj.id, reseller_id=obj.reseller_id
    )


# ----------------------------------------------------------------------------- sites


SITE_SORTS = {
    "name": SortOption(Site.name, "str"),
    "created_at": SortOption(Site.created_at, "datetime"),
}


async def list_sites(
    session: AsyncSession,
    p: Principal,
    *,
    customer_id: uuid.UUID | None,
    q: str | None,
    sort: str,
    direction: Direction,
    limit: int,
    cursor: str | None,
) -> PageResult[Site]:
    p.require("sites.read")
    stmt = select(Site).where(
        Site.deleted_at.is_(None), reseller_scope(p, Site.reseller_id), customer_scope(p, Site.customer_id)
    )
    if customer_id:
        stmt = stmt.where(Site.customer_id == customer_id)
    if q:
        stmt = stmt.where(or_(Site.name.ilike(_like(q)), Site.address.ilike(_like(q))))
    return await paginate(
        session,
        stmt,
        id_column=Site.id,
        sort_options=SITE_SORTS,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )


async def get_site(session: AsyncSession, p: Principal, site_id: uuid.UUID) -> Site:
    p.require("sites.read")
    obj = await session.get(Site, site_id)
    if (
        obj is None
        or obj.deleted_at is not None
        or not p.can_access_customer(obj.reseller_id, obj.customer_id)
    ):
        raise not_found("Local")
    return obj


async def create_site(session: AsyncSession, settings: Settings, p: Principal, data: SiteIn) -> Site:
    p.require("sites.write")
    customer = await get_customer(session, p, data.customer_id)
    fields = data.model_dump(exclude={"collection_config"})
    obj = Site(
        reseller_id=customer.reseller_id,
        collection_config=data.collection_config.model_dump(exclude_none=True),
        **fields,
    )
    session.add(obj)
    await session.flush()
    await add_default_credential(session, settings, obj)
    await audit.record(
        session,
        p,
        action="create",
        entity="site",
        entity_id=obj.id,
        reseller_id=obj.reseller_id,
        after=audit.snapshot(obj),
    )
    return obj


async def update_site(session: AsyncSession, p: Principal, site_id: uuid.UUID, data: SiteUpdate) -> Site:
    p.require("sites.write")
    obj = await get_site(session, p, site_id)
    before = audit.snapshot(obj)
    changes = data.model_dump(exclude_unset=True)
    if "collection_config" in changes and data.collection_config is not None:
        changes["collection_config"] = data.collection_config.model_dump(exclude_none=True)
    _apply(obj, changes)
    await session.flush()
    if "collection_config" in changes:
        await bump_site_config(session, obj.id)
    b, a = audit.diff(before, audit.snapshot(obj))
    await audit.record(
        session,
        p,
        action="update",
        entity="site",
        entity_id=obj.id,
        reseller_id=obj.reseller_id,
        before=b,
        after=a,
    )
    return obj


async def delete_site(session: AsyncSession, p: Principal, site_id: uuid.UUID) -> None:
    p.require("sites.write")
    obj = await get_site(session, p, site_id)
    for model, label in ((Agent, "coletores"), (Device, "equipamentos")):
        count = (
            await session.execute(
                select(func.count())
                .select_from(model)
                .where(model.site_id == obj.id, model.deleted_at.is_(None))
            )
        ).scalar_one()
        if count:
            raise conflict("site_in_use", f"O local ainda tem {label} cadastrados")
    obj.deleted_at = _now()
    await audit.record(
        session, p, action="delete", entity="site", entity_id=obj.id, reseller_id=obj.reseller_id
    )
