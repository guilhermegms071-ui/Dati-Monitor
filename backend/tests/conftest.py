"""Shared fixtures. Tests run against the real local PostgreSQL database dati_test (recreated per session)."""

import base64
import importlib.util
import os
import socket
import sys
import tomllib
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.product import REPO_ROOT
from app.core.security import hash_password
from tests.release_helpers import TEST_RELEASE_PUBLIC_B64

# Versão declarada em backend/pyproject.toml: os testes conferem a versão exposta contra ela.
PYPROJECT_VERSION: str = tomllib.loads(
    (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
)["project"]["version"]


def _load_repo_env() -> None:
    """Loads the repository .env into os.environ without overriding real environment variables."""
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_repo_env()
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
if not TEST_DATABASE_URL:
    pytest.exit(
        "TEST_DATABASE_URL não definida (.env ou ambiente). Rode scripts\\setup-db.ps1.", returncode=2
    )
# Qualquer código que chame get_settings() (Alembic, CLI) aponta para o banco de teste.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("JWT_SECRET", "test-" + "x" * 60)
os.environ.setdefault("MASTER_KEY", base64.b64encode(b"k" * 32).decode())
get_settings.cache_clear()

# Tabelas de sistema preservadas entre testes (preenchidas pela migração/bootstrap).
_KEEP_TABLES = {"alembic_version", "roles", "role_permissions", "brands", "read_profiles"}


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses do script precisam do módulo registrado
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Recreates the public schema and runs every migration up, down and up again (reversibility check)."""
    import asyncio  # noqa: PLC0415

    from alembic import command  # noqa: PLC0415 - import tardio: env de teste já configurado
    from app.cli import alembic_config  # noqa: PLC0415

    async def _reset() -> None:
        engine = make_engine(TEST_DATABASE_URL)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(_reset())
    cfg = alembic_config(TEST_DATABASE_URL)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    async def _roles() -> None:
        from app.services.bootstrap import sync_roles  # noqa: PLC0415
        from app.services.catalog import sync_brands, sync_profiles  # noqa: PLC0415

        engine = make_engine(TEST_DATABASE_URL)
        async with make_sessionmaker(engine)() as session:
            await sync_roles(session)
            await sync_brands(session)
            await sync_profiles(session)
            await session.commit()
        await engine.dispose()

    asyncio.run(_roles())
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def mail_catcher(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    mod = load_script("smtp_catcher")
    catcher = mod.Catcher("127.0.0.1", free_port(), free_port(), tmp_path_factory.mktemp("mail"))
    catcher.start()
    try:
        yield catcher
    finally:
        catcher.stop()


@pytest.fixture(scope="session")
def test_settings(migrated_db: str, mail_catcher: Any, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return Settings(
        storage_dir=tmp_path_factory.mktemp("storage"),
        gateway_sweep_seconds=1,
        database_url=migrated_db,
        app_env="test",
        db_check_interval_seconds=1,
        cookie_secure=False,
        smtp_host="127.0.0.1",
        smtp_port=mail_catcher.smtp.port,
        login_rate_limit_per_minute=1000,
        public_base_url="http://portal.test",
        release_public_key=TEST_RELEASE_PUBLIC_B64,
    )


@pytest.fixture(scope="session")
def unreachable_settings() -> Settings:
    # Porta 1 do loopback: conexão recusada imediatamente, simulando banco fora do ar.
    return Settings(
        database_url="postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none",
        app_env="test",
        db_check_interval_seconds=1,
    )


@pytest.fixture
async def engine(test_settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = make_engine(test_settings.database_url)
    yield eng
    await eng.dispose()


@pytest.fixture
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(engine)


@pytest.fixture
async def clean_db(engine: AsyncEngine, mail_catcher: Any) -> None:
    """Empties every table (except roles) before the test, including append-only ones."""
    async with engine.begin() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition"
            )
        )
        tables = [r[0] for r in rows if r[0] not in _KEEP_TABLES]
        await conn.execute(text("SET LOCAL dati.maintenance = 'on'"))
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    # read_profiles referencia users: o CASCADE também a esvazia; ressincroniza os perfis de /profiles.
    from app.services.catalog import sync_profiles  # noqa: PLC0415

    async with make_sessionmaker(engine)() as session:
        await sync_profiles(session)
        await session.commit()
    mail_catcher.store.clear()


@pytest.fixture
async def api_app(test_settings: Settings, clean_db: None) -> AsyncIterator[FastAPI]:
    from app.api.main import create_app  # noqa: PLC0415

    app = create_app(test_settings, run_bootstrap=False)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=api_app, client=("203.0.113.10", 5555))
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as c:
        yield c


# ----------------------------------------------------------------------------- factories


@dataclass
class Tenant:
    reseller_id: uuid.UUID
    company_id: uuid.UUID
    customer_id: uuid.UUID
    site_id: uuid.UUID
    admin_email: str
    admin_password: str


class Factory:
    """Creates rows directly in the database (bypassing the API) for test setup."""

    PASSWORD = "Senha-Forte-123"

    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self.maker = maker

    async def tenant(
        self, name: str = "Revenda A", admin_role: str = "reseller_admin", *, auto_activate: bool = True
    ) -> Tenant:
        """Revenda completa para os testes. O Local ativa automaticamente os equipamentos descobertos
        (os testes de parque/leitura partem de equipamentos ativos); os testes de Descobertas usam
        auto_activate=False, que é o padrão do produto (seção 16.1)."""
        from app.models import Company, Customer, Reseller, Site  # noqa: PLC0415

        async with self.maker() as s:
            reseller = Reseller(name=name)
            s.add(reseller)
            await s.flush()
            company = Company(reseller_id=reseller.id, legal_name=f"{name} Empresa")
            s.add(company)
            await s.flush()
            customer = Customer(
                reseller_id=reseller.id, company_id=company.id, name=f"{name} Cliente", erp_code=f"E-{name}"
            )
            s.add(customer)
            await s.flush()
            site = Site(
                reseller_id=reseller.id,
                customer_id=customer.id,
                name=f"{name} Local",
                auto_activate_devices=auto_activate,
            )
            s.add(site)
            await s.commit()
        email = f"admin@{uuid.uuid4().hex[:8]}.test"
        await self.user(reseller.id, email=email, role=admin_role)
        return Tenant(reseller.id, company.id, customer.id, site.id, email, self.PASSWORD)

    async def customer(self, reseller_id: uuid.UUID, company_id: uuid.UUID, name: str) -> uuid.UUID:
        from app.models import Customer  # noqa: PLC0415

        async with self.maker() as s:
            customer = Customer(reseller_id=reseller_id, company_id=company_id, name=name)
            s.add(customer)
            await s.commit()
            return customer.id

    async def user(
        self,
        reseller_id: uuid.UUID,
        *,
        email: str | None = None,
        role: str = "operator",
        customer_id: uuid.UUID | None = None,
        password: str | None = None,
        must_change_password: bool = False,
        active: bool = True,
    ) -> tuple[uuid.UUID, str]:
        from app.models import User  # noqa: PLC0415

        email = email or f"user-{uuid.uuid4().hex[:8]}@example.test"
        async with self.maker() as s:
            user = User(
                reseller_id=reseller_id,
                customer_id=customer_id,
                name="Usuário de Teste",
                email=email,
                password_hash=hash_password(password or self.PASSWORD),
                role_code=role,
                must_change_password=must_change_password,
                active=active,
            )
            s.add(user)
            await s.commit()
            return user.id, email


@pytest.fixture
def factory(sessionmaker: async_sessionmaker[AsyncSession], clean_db: None) -> Factory:
    return Factory(sessionmaker)


async def login(client: httpx.AsyncClient, email: str, password: str = Factory.PASSWORD, **extra: Any) -> str:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password, **extra})
    assert resp.status_code == 200, resp.text
    token: str = resp.json()["access_token"]
    return token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
