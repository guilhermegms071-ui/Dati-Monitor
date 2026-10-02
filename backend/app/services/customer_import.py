"""Importação de clientes por CSV (PROMPT 16.9): separator `;`, header with the column names below, line by
line validation with the same rules as the forms, and an error report. All or nothing: with any error,
nothing is written. Several lines of the same customer (same código ERP, CNPJ or name) become one customer
with several sites."""

import csv
import io
import unicodedata
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import bad_request, forbidden
from app.core.principal import Principal
from app.models import Customer
from app.schemas.tenancy import CustomerImportError, CustomerImportResult, CustomerIn, SiteAddress, SiteIn
from app.services import tenancy

MAX_BYTES = 2 * 1024 * 1024
MAX_LINES = 5000
DEFAULT_SITE = "Principal"
IGNORED = "coluna desconhecida (ignorada)"

# Nome normalizado da coluna → campo. Aceita acentos, maiúsculas e variações comuns.
COLUMNS: dict[str, str] = {
    "nome": "name",
    "cliente": "name",
    "razao social": "name",
    "cnpj": "cnpj",
    "codigo erp": "erp_code",
    "erp": "erp_code",
    "contato": "contact_name",
    "telefone": "phone",
    "email": "email",
    "e-mail": "email",
    "local": "site_name",
    "cep": "cep",
    "logradouro": "street",
    "endereco": "street",
    "rua": "street",
    "numero": "number",
    "complemento": "complement",
    "bairro": "district",
    "cidade": "city",
    "uf": "state",
    "estado": "state",
    "latitude": "latitude",
    "longitude": "longitude",
}
LABELS = {
    "name": "nome",
    "cnpj": "cnpj",
    "erp_code": "código erp",
    "contact_name": "contato",
    "phone": "telefone",
    "email": "e-mail",
    "site_name": "local",
    "cep": "cep",
    "street": "logradouro",
    "number": "número",
    "complement": "complemento",
    "district": "bairro",
    "city": "cidade",
    "state": "uf",
    "latitude": "latitude",
    "longitude": "longitude",
}
CUSTOMER_FIELDS = ("name", "cnpj", "erp_code", "contact_name", "phone", "email")
ADDRESS_FIELDS = (
    "cep",
    "street",
    "number",
    "complement",
    "district",
    "city",
    "state",
    "latitude",
    "longitude",
)
TEMPLATE = (
    "nome;cnpj;código erp;contato;telefone;e-mail;local;cep;logradouro;número;complemento;bairro;cidade;uf;"
    "latitude;longitude\r\n"
    "Escola Exemplo;11.222.333/0001-81;C001;Maria;(11) 3333-4444;ti@escola.exemplo;Sede;01310-100;"
    "Av. Paulista;1000;;Bela Vista;São Paulo;SP;-23,561;-46,656\r\n"
)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _decode(raw: bytes) -> str:
    """UTF-8 (with or without BOM) or, as Excel in pt-BR saves it, Windows-1252."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _decimal(v: str) -> Decimal:
    try:
        return Decimal(v.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("número inválido") from exc


@dataclass
class _Site:
    line: int
    name: str
    address: dict[str, Any]


@dataclass
class _Customer:
    line: int
    data: dict[str, Any]
    sites: list[_Site] = field(default_factory=list)


def _errors_of(exc: ValidationError, line: int, prefix: str = "") -> list[CustomerImportError]:
    out = []
    for e in exc.errors():
        loc = str(e["loc"][0]) if e["loc"] else ""
        msg = str(e["msg"]).removeprefix("Value error, ")
        out.append(CustomerImportError(line=line, column=LABELS.get(loc, prefix or loc), message=msg))
    return out


def _header(header: list[str]) -> tuple[dict[int, str], list[CustomerImportError]]:
    mapping: dict[int, str] = {}
    warnings = []
    for i, name in enumerate(header):
        key = COLUMNS.get(_norm(name))
        if key is not None:
            mapping[i] = key
        elif name.strip():
            warnings.append(CustomerImportError(line=1, column=name.strip(), message=IGNORED))
    if "name" not in mapping.values():
        raise bad_request(
            "csv_header",
            "Cabeçalho sem a coluna 'nome'. Use ';' como separador e a primeira linha com os nomes "
            "das colunas.",
        )
    return mapping, warnings


def _address(n: int, values: dict[str, str]) -> tuple[dict[str, Any], list[CustomerImportError]]:
    raw: dict[str, Any] = {k: values[k] for k in ADDRESS_FIELDS if k in values}
    try:
        for k in ("latitude", "longitude"):
            if k in raw:
                raw[k] = _decimal(raw[k])
        return SiteAddress.model_validate(raw).model_dump(exclude_none=True), []
    except ValidationError as exc:
        return {}, _errors_of(exc, n)
    except ValueError as exc:
        return {}, [CustomerImportError(line=n, column="latitude/longitude", message=str(exc))]


def _customer_key(data: dict[str, Any]) -> str:
    if data.get("erp_code"):
        return f"erp:{data['erp_code']}"
    if data.get("cnpj"):
        return f"cnpj:{data['cnpj']}"
    return f"name:{_norm(data['name'])}"


def _line(
    n: int, values: dict[str, str], company_id: uuid.UUID, customers: dict[str, _Customer]
) -> list[CustomerImportError]:
    try:
        cust = CustomerIn.model_validate(
            {"company_id": company_id, **{k: values[k] for k in CUSTOMER_FIELDS if k in values}}
        )
    except ValidationError as exc:
        return _errors_of(exc, n)
    address, errors = _address(n, values)
    if errors:
        return errors
    data = cust.model_dump(
        exclude={"company_id", "toner_thresholds", "active", "toner_monitoring"}, exclude_none=True
    )
    entry = customers.setdefault(_customer_key(data), _Customer(n, data))
    if entry.line != n and entry.data != data:
        msg = f"dados do cliente diferentes da linha {entry.line} (mesmo código ERP/CNPJ/nome)"
        return [CustomerImportError(line=n, column="cliente", message=msg)]
    # Todo cliente tem ao menos um local; sem nome de local, o endereço vai para o local "Principal".
    site_name = values.get("site_name") or (DEFAULT_SITE if address or not entry.sites else None)
    if site_name is None:
        return []
    if any(_norm(s.name) == _norm(site_name) for s in entry.sites):
        return [CustomerImportError(line=n, column="local", message="local repetido para este cliente")]
    entry.sites.append(_Site(n, site_name, address))
    return []


def parse(raw: bytes, company_id: uuid.UUID) -> tuple[list[_Customer], list[CustomerImportError], int]:
    if len(raw) > MAX_BYTES:
        raise bad_request("csv_too_large", "Arquivo grande demais (máx. 2 MB)")
    reader = csv.reader(io.StringIO(_decode(raw)), delimiter=";")
    try:
        mapping, errors = _header(next(reader))
    except StopIteration as exc:
        raise bad_request("csv_empty", "Arquivo vazio") from exc
    customers: dict[str, _Customer] = {}
    lines = 0
    for n, row in enumerate(reader, start=2):
        if not any(c.strip() for c in row):
            continue
        lines += 1
        if lines > MAX_LINES:
            raise bad_request("csv_too_many_lines", f"Máximo de {MAX_LINES} linhas por arquivo")
        values = {mapping[i]: c.strip() for i, c in enumerate(row) if i in mapping and c.strip()}
        errors += _line(n, values, company_id, customers)
    return list(customers.values()), errors, lines


async def _conflicts(
    session: AsyncSession, reseller_id: uuid.UUID, customers: list[_Customer]
) -> list[CustomerImportError]:
    codes = {c.data["erp_code"]: c.line for c in customers if c.data.get("erp_code")}
    cnpjs = {c.data["cnpj"]: c.line for c in customers if c.data.get("cnpj")}
    if not codes and not cnpjs:
        return []
    rows = await session.execute(
        select(Customer.name, Customer.erp_code, Customer.cnpj).where(
            Customer.reseller_id == reseller_id,
            Customer.deleted_at.is_(None),
            or_(Customer.erp_code.in_(list(codes)), Customer.cnpj.in_(list(cnpjs))),
        )
    )
    out = []
    for name, erp_code, cnpj in rows.tuples():
        if erp_code in codes:
            out.append(
                CustomerImportError(
                    line=codes[erp_code], column="código erp", message=f"já cadastrado ({name})"
                )
            )
        elif cnpj in cnpjs:
            out.append(
                CustomerImportError(line=cnpjs[cnpj], column="cnpj", message=f"já cadastrado ({name})")
            )
    return out


async def run(
    session: AsyncSession,
    settings: Settings,
    p: Principal,
    raw: bytes,
    *,
    company_id: uuid.UUID,
    dry_run: bool,
) -> CustomerImportResult:
    p.require("customers.create")
    if p.customer_id is not None:
        raise forbidden("Usuários com escopo de cliente não importam clientes")
    company = await tenancy.company_in_scope(session, p, company_id)
    customers, errors, lines = parse(raw, company.id)
    errors += await _conflicts(session, company.reseller_id, customers)
    blocking = [e for e in errors if e.message != IGNORED]
    result = CustomerImportResult(
        dry_run=dry_run,
        imported=False,
        lines=lines,
        customers=len(customers),
        sites=sum(len(c.sites) for c in customers),
        errors=sorted(errors, key=lambda e: (e.line, e.column)),
    )
    if dry_run or blocking or not customers:
        return result
    for c in customers:
        obj = await tenancy.create_customer(session, p, CustomerIn(company_id=company.id, **c.data))
        for s in c.sites:
            await tenancy.create_site(
                session, settings, p, SiteIn(customer_id=obj.id, name=s.name, **s.address)
            )
    total = (
        await session.execute(
            select(func.count()).select_from(Customer).where(Customer.company_id == company.id)
        )
    ).scalar_one()
    result.imported = True
    result.company_customers = total
    return result
