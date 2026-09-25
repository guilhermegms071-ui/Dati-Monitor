"""/api/v1 resellers, companies, customers and sites."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import PrincipalDep, SessionDep, SettingsDep
from app.models import Customer
from app.schemas.common import ERROR_RESPONSES, Page
from app.schemas.tenancy import (
    CompanyIn,
    CompanyOut,
    CompanyUpdate,
    CustomerIn,
    CustomerOut,
    CustomerUpdate,
    ResellerIn,
    ResellerOut,
    ResellerUpdate,
    SiteIn,
    SiteOut,
    SiteUpdate,
)
from app.services import tenancy as svc
from app.services.export import ExportColumn, ExportFormat, export_response
from app.services.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Direction

router = APIRouter(responses=ERROR_RESPONSES)

Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
Q = Annotated[str | None, Query(max_length=200, description="Pesquisa por nome/código")]


# ----------------------------------------------------------------------------- resellers


@router.get("/resellers", response_model=Page[ResellerOut], tags=["revendas"])
async def list_resellers(
    p: PrincipalDep,
    session: SessionDep,
    q: Q = None,
    sort: str = "name",
    direction: Direction = "asc",
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> Page[ResellerOut]:
    page = await svc.list_resellers(
        session, p, q=q, sort=sort, direction=direction, limit=limit, cursor=cursor
    )
    return Page(items=[ResellerOut.model_validate(r) for r in page.items], next_cursor=page.next_cursor)


@router.post("/resellers", response_model=ResellerOut, status_code=status.HTTP_201_CREATED, tags=["revendas"])
async def create_reseller(body: ResellerIn, p: PrincipalDep, session: SessionDep) -> ResellerOut:
    obj = await svc.create_reseller(session, p, body)
    await session.commit()
    return ResellerOut.model_validate(obj)


@router.get("/resellers/{reseller_id}", response_model=ResellerOut, tags=["revendas"])
async def get_reseller(reseller_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> ResellerOut:
    return ResellerOut.model_validate(await svc.get_reseller(session, p, reseller_id))


@router.patch("/resellers/{reseller_id}", response_model=ResellerOut, tags=["revendas"])
async def update_reseller(
    reseller_id: uuid.UUID, body: ResellerUpdate, p: PrincipalDep, session: SessionDep
) -> ResellerOut:
    obj = await svc.update_reseller(session, p, reseller_id, body)
    await session.commit()
    return ResellerOut.model_validate(obj)


@router.delete("/resellers/{reseller_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["revendas"])
async def delete_reseller(reseller_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> Response:
    await svc.delete_reseller(session, p, reseller_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- companies


@router.get("/companies", response_model=Page[CompanyOut], tags=["empresas"])
async def list_companies(
    p: PrincipalDep,
    session: SessionDep,
    q: Q = None,
    reseller_id: uuid.UUID | None = None,
    sort: str = "legal_name",
    direction: Direction = "asc",
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> Page[CompanyOut]:
    page = await svc.list_companies(
        session, p, q=q, reseller_id=reseller_id, sort=sort, direction=direction, limit=limit, cursor=cursor
    )
    return Page(items=[CompanyOut.model_validate(c) for c in page.items], next_cursor=page.next_cursor)


@router.post("/companies", response_model=CompanyOut, status_code=status.HTTP_201_CREATED, tags=["empresas"])
async def create_company(body: CompanyIn, p: PrincipalDep, session: SessionDep) -> CompanyOut:
    obj = await svc.create_company(session, p, body)
    await session.commit()
    return CompanyOut.model_validate(obj)


@router.get("/companies/{company_id}", response_model=CompanyOut, tags=["empresas"])
async def get_company(company_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> CompanyOut:
    return CompanyOut.model_validate(await svc.get_company(session, p, company_id))


@router.patch("/companies/{company_id}", response_model=CompanyOut, tags=["empresas"])
async def update_company(
    company_id: uuid.UUID, body: CompanyUpdate, p: PrincipalDep, session: SessionDep
) -> CompanyOut:
    obj = await svc.update_company(session, p, company_id, body)
    await session.commit()
    return CompanyOut.model_validate(obj)


@router.delete("/companies/{company_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["empresas"])
async def delete_company(company_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> Response:
    await svc.delete_company(session, p, company_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- customers


CUSTOMER_COLUMNS: list[ExportColumn[Customer]] = [
    ExportColumn("Nome", lambda c: c.name),
    ExportColumn("CNPJ", lambda c: c.cnpj),
    ExportColumn("Código ERP", lambda c: c.erp_code),
    ExportColumn("Contato", lambda c: c.contact_name),
    ExportColumn("Telefone", lambda c: c.phone),
    ExportColumn("E-mail", lambda c: c.email),
    ExportColumn("Ativo", lambda c: c.active),
    ExportColumn("Criado em", lambda c: c.created_at),
]


@router.get("/customers", response_model=Page[CustomerOut], tags=["clientes"])
async def list_customers(
    p: PrincipalDep,
    session: SessionDep,
    q: Q = None,
    company_id: uuid.UUID | None = None,
    active: bool | None = None,
    sort: str = "name",
    direction: Direction = "asc",
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> Page[CustomerOut]:
    page = await svc.list_customers(
        session,
        p,
        q=q,
        company_id=company_id,
        active=active,
        sort=sort,
        direction=direction,
        limit=limit,
        cursor=cursor,
    )
    return Page(items=[CustomerOut.model_validate(c) for c in page.items], next_cursor=page.next_cursor)


@router.get(
    "/customers/export", tags=["clientes"], summary="Exportar clientes (CSV/XLSX, mesmos filtros da lista)"
)
async def export_customers(
    p: PrincipalDep,
    session: SessionDep,
    format: ExportFormat = "csv",  # noqa: A002 - nome do parâmetro na URL
    q: Q = None,
    company_id: uuid.UUID | None = None,
    active: bool | None = None,
) -> Response:
    rows = await svc.export_customers(session, p, q=q, company_id=company_id, active=active)
    return export_response(rows, CUSTOMER_COLUMNS, fmt=format, basename="clientes")


@router.post("/customers", response_model=CustomerOut, status_code=status.HTTP_201_CREATED, tags=["clientes"])
async def create_customer(body: CustomerIn, p: PrincipalDep, session: SessionDep) -> CustomerOut:
    obj = await svc.create_customer(session, p, body)
    await session.commit()
    return CustomerOut.model_validate(obj)


@router.get("/customers/{customer_id}", response_model=CustomerOut, tags=["clientes"])
async def get_customer(customer_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> CustomerOut:
    return CustomerOut.model_validate(await svc.get_customer(session, p, customer_id))


@router.patch("/customers/{customer_id}", response_model=CustomerOut, tags=["clientes"])
async def update_customer(
    customer_id: uuid.UUID, body: CustomerUpdate, p: PrincipalDep, session: SessionDep
) -> CustomerOut:
    obj = await svc.update_customer(session, p, customer_id, body)
    await session.commit()
    return CustomerOut.model_validate(obj)


@router.delete("/customers/{customer_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["clientes"])
async def delete_customer(customer_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> Response:
    await svc.delete_customer(session, p, customer_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- sites


@router.get("/sites", response_model=Page[SiteOut], tags=["locais"])
async def list_sites(
    p: PrincipalDep,
    session: SessionDep,
    customer_id: uuid.UUID | None = None,
    q: Q = None,
    sort: str = "name",
    direction: Direction = "asc",
    limit: Limit = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
) -> Page[SiteOut]:
    page = await svc.list_sites(
        session, p, customer_id=customer_id, q=q, sort=sort, direction=direction, limit=limit, cursor=cursor
    )
    return Page(items=[SiteOut.model_validate(s) for s in page.items], next_cursor=page.next_cursor)


@router.post("/sites", response_model=SiteOut, status_code=status.HTTP_201_CREATED, tags=["locais"])
async def create_site(body: SiteIn, p: PrincipalDep, session: SessionDep, settings: SettingsDep) -> SiteOut:
    obj = await svc.create_site(session, settings, p, body)
    await session.commit()
    return SiteOut.model_validate(obj)


@router.get("/sites/{site_id}", response_model=SiteOut, tags=["locais"])
async def get_site(site_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> SiteOut:
    return SiteOut.model_validate(await svc.get_site(session, p, site_id))


@router.patch("/sites/{site_id}", response_model=SiteOut, tags=["locais"])
async def update_site(site_id: uuid.UUID, body: SiteUpdate, p: PrincipalDep, session: SessionDep) -> SiteOut:
    obj = await svc.update_site(session, p, site_id, body)
    await session.commit()
    return SiteOut.model_validate(obj)


@router.delete("/sites/{site_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["locais"])
async def delete_site(site_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> Response:
    await svc.delete_site(session, p, site_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
