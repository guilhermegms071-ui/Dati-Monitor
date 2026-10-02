"""Importação de clientes por CSV (PROMPT 16.9): `;` separator, UTF-8 or Windows-1252, validation line by
line with the form rules, error report, all or nothing, several sites per customer, template download."""

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditLog, Customer, Site, SnmpCredential
from tests.conftest import Factory, auth, login

HEADER = (
    "Nome;CNPJ;Código ERP;Contato;E-mail;Local;CEP;Logradouro;Número;Cidade;UF;Latitude;Longitude;Obs\r\n"
)
GOOD = (
    HEADER
    + "Escola Azul;11.222.333/0001-81;C001;Ana;ti@azul.test;Sede;01310-100;Av. Paulista;1000;São Paulo;SP;-23,561;-46,656;x\r\n"
    + "Escola Azul;11.222.333/0001-81;C001;Ana;ti@azul.test;Anexo;;;;Campinas;SP;;;\r\n"
    + "Padaria Sol;;;;;;;;;;;;;\r\n"
    + ";;;;;;;;;;;;;\r\n"
)  # fmt: skip


async def post(
    client: httpx.AsyncClient, token: str, company_id: str, body: bytes, *, dry_run: bool
) -> httpx.Response:
    return await client.post(
        "/api/v1/customers/import",
        params={"company_id": company_id, "dry_run": str(dry_run).lower()},
        content=body,
        headers={**auth(token), "Content-Type": "text/csv"},
    )


async def test_validate_then_import(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    company = str(tenant.company_id)

    dry = (await post(client, admin, company, GOOD.encode("utf-8"), dry_run=True)).json()
    assert (dry["dry_run"], dry["imported"], dry["lines"], dry["customers"], dry["sites"]) == (
        True,
        False,
        3,
        2,
        3,
    )
    assert dry["errors"] == [{"line": 1, "column": "Obs", "message": "coluna desconhecida (ignorada)"}]
    async with sessionmaker() as s:
        before = (await s.execute(select(func.count()).select_from(Customer))).scalar_one()

    # Excel em pt-BR salva em Windows-1252: também é aceito.
    done = (await post(client, admin, company, GOOD.encode("cp1252"), dry_run=False)).json()
    assert done["imported"] is True
    assert done["company_customers"] == before + 2
    async with sessionmaker() as s:
        azul = (await s.execute(select(Customer).where(Customer.erp_code == "C001"))).scalar_one()
        assert (azul.cnpj, azul.email, azul.contact_name) == ("11222333000181", "ti@azul.test", "Ana")
        sites = {
            x.name: x for x in (await s.execute(select(Site).where(Site.customer_id == azul.id))).scalars()
        }
        assert set(sites) == {"Sede", "Anexo"}
        assert (sites["Sede"].cep, sites["Sede"].city, str(sites["Sede"].latitude)) == (
            "01310100",
            "São Paulo",
            "-23.561000",
        )
        sol = (await s.execute(select(Customer).where(Customer.name == "Padaria Sol"))).scalar_one()
        sol_sites = (await s.execute(select(Site.name).where(Site.customer_id == sol.id))).scalars().all()
        assert sol_sites == ["Principal"], "todo cliente ganha ao menos um local"
        creds = (await s.execute(select(func.count()).select_from(SnmpCredential))).scalar_one()
        assert creds >= 3, "os locais importados ganham a credencial SNMP padrão como no formulário"
        audited = (
            await s.execute(select(func.count()).select_from(AuditLog).where(AuditLog.entity == "customer"))
        ).scalar_one()
        assert audited == 2

    # Importar de novo: o código ERP já existe → nada é gravado.
    again = (await post(client, admin, company, GOOD.encode("utf-8"), dry_run=False)).json()
    assert again["imported"] is False
    assert {"line": 2, "column": "código erp", "message": "já cadastrado (Escola Azul)"} in again["errors"]


async def test_line_errors_block_everything(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    bad = (
        HEADER
        + "Ok Ltda;;K1;;;;;;;;;;;\r\n"
        + "X;11.111.111/1111-11;;;não-é-email;;99;;;;ZZ;abc;;\r\n"
        + "Ok Ltda;;K1;Outro contato;;;;;;;;;;\r\n"
    )  # fmt: skip
    resp = (await post(client, admin, str(tenant.company_id), bad.encode(), dry_run=False)).json()
    assert resp["imported"] is False
    cols = {(e["line"], e["column"]) for e in resp["errors"]}
    assert (3, "nome") in cols
    assert (4, "cliente") in cols, "mesmo código ERP com dados diferentes"
    async with sessionmaker() as s:
        assert not (await s.execute(select(Customer).where(Customer.erp_code == "K1"))).scalars().all()

    no_header = await post(client, admin, str(tenant.company_id), b"a,b,c\r\n1,2,3\r\n", dry_run=True)
    assert no_header.status_code == 400
    assert no_header.json()["detail"]["code"] == "csv_header"
    empty = await post(client, admin, str(tenant.company_id), b"", dry_run=True)
    assert empty.json()["detail"]["code"] == "csv_empty"


async def test_scope_and_template(client: httpx.AsyncClient, factory: Factory) -> None:
    tenant = await factory.tenant()
    other = await factory.tenant("Outra empresa")
    stranger = await login(client, other.admin_email)
    resp = await post(client, stranger, str(tenant.company_id), GOOD.encode(), dry_run=True)
    assert resp.status_code == 404
    _, tech_email = await factory.user(tenant.reseller_id, role="technician")
    tech = await login(client, tech_email)
    assert (await post(client, tech, str(tenant.company_id), GOOD.encode(), dry_run=True)).status_code == 403

    admin = await login(client, tenant.admin_email)
    tpl = await client.get("/api/v1/customers/import/template", headers=auth(admin))
    assert tpl.status_code == 200
    text = tpl.content.decode("utf-8-sig")
    assert text.startswith("nome;cnpj;código erp")
    check = (await post(client, admin, str(tenant.company_id), tpl.content, dry_run=True)).json()
    assert check["errors"] == []
    assert (check["customers"], check["sites"]) == (1, 1)


async def test_site_map_status(
    client: httpx.AsyncClient, factory: Factory, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    from decimal import Decimal  # noqa: PLC0415

    from sqlalchemy import update  # noqa: PLC0415

    from tests.agent_helpers import enrolled_agent  # noqa: PLC0415

    tenant = await factory.tenant()
    admin = await login(client, tenant.admin_email)
    rows = (await client.get("/api/v1/sites/map", headers=auth(admin))).json()
    assert [(r["name"], r["status"], r["latitude"]) for r in rows] == [("Revenda A Local", "no_agent", None)]
    agent = await enrolled_agent(client, tenant)
    await agent.heartbeat()
    await agent.send([agent.reading("MAP1", {"total": 10, "mono": 10, "color": 0})])
    async with sessionmaker() as s:
        await s.execute(update(Site).values(latitude=Decimal("-23.5"), longitude=Decimal("-46.6")))
        await s.commit()
    row = (await client.get("/api/v1/sites/map", headers=auth(admin))).json()[0]
    assert (row["status"], row["devices"], row["agents_online"]) == ("ok", 1, 1)
    assert (row["latitude"], row["longitude"]) == ("-23.500000", "-46.600000")
    async with sessionmaker() as s:
        from app.models import Agent, Device  # noqa: PLC0415

        await s.execute(update(Device).values(disconnected=True))
        await s.commit()
        assert (await client.get("/api/v1/sites/map", headers=auth(admin))).json()[0]["status"] == "warning"
        await s.execute(update(Agent).values(state="offline"))
        await s.commit()
    assert (await client.get("/api/v1/sites/map", headers=auth(admin))).json()[0]["status"] == "offline"
    other = await factory.tenant("Outra empresa")
    stranger = await login(client, other.admin_email)
    names = [r["name"] for r in (await client.get("/api/v1/sites/map", headers=auth(stranger))).json()]
    assert names == ["Outra empresa Local"]
