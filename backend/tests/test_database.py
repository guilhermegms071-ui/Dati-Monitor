"""Schema-level guarantees: append-only readings/audit, monthly partitions, migrations in sync, bootstrap/seed."""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alembic import command
from app.cli import alembic_config
from app.core.config import Settings
from app.models import (
    AuditLog,
    Company,
    Customer,
    Device,
    Reading,
    ReadingIdempotency,
    Reseller,
    RolePermission,
    Site,
    User,
)
from app.services.bootstrap import ensure_bootstrap, seed_dev, sync_roles
from app.services.partitions import default_partition_rows, ensure_partitions, month_starts
from tests.conftest import Factory

pytestmark = pytest.mark.usefixtures("clean_db")


def test_models_and_migrations_are_in_sync(migrated_db: str) -> None:
    cfg = alembic_config(migrated_db)
    cfg.attributes["configure_logger"] = False
    command.check(cfg)  # falha se o modelo divergir das migrações


async def _device(maker: async_sessionmaker[AsyncSession]) -> tuple[uuid.UUID, uuid.UUID]:
    f = Factory(maker)
    t = await f.tenant()
    async with maker() as s:
        d = Device(reseller_id=t.reseller_id, site_id=t.site_id, customer_id=t.customer_id, serial="IMUT-1")
        s.add(d)
        await s.commit()
        return t.reseller_id, d.id


async def test_readings_are_append_only(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    reseller_id, device_id = await _device(sessionmaker)
    async with sessionmaker() as s:
        s.add(
            Reading(
                reseller_id=reseller_id,
                device_id=device_id,
                read_at=datetime.now(UTC),
                idempotency_key="k1",
                total=100,
            )
        )
        await s.commit()
    for stmt in (
        "UPDATE readings SET total = 1",
        "DELETE FROM readings",
        "TRUNCATE readings CASCADE",
        f"TRUNCATE readings_{datetime.now(UTC):%Y_%m} CASCADE",
    ):
        async with sessionmaker() as s:
            with pytest.raises(DBAPIError, match="somente inserção"):
                await s.execute(text(stmt))
    async with sessionmaker() as s:
        assert (await s.execute(select(Reading.total))).scalar_one() == 100


async def test_audit_log_is_append_only(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as s:
        s.add(AuditLog(action="x", entity="y"))
        await s.commit()
    async with sessionmaker() as s:
        with pytest.raises(DBAPIError, match="somente inserção"):
            await s.execute(text("UPDATE audit_log SET action = 'z'"))
    async with sessionmaker() as s:
        with pytest.raises(DBAPIError, match="somente inserção"):
            await s.execute(text("DELETE FROM audit_log"))


def test_month_starts() -> None:
    assert month_starts(date(2026, 12, 15), 1, 2) == [
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    ]
    assert month_starts(date(2027, 1, 31), 1, 0) == [date(2026, 12, 1), date(2027, 1, 1)]


async def test_partitions_move_rows_from_default_without_losing_readings(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    reseller_id, device_id = await _device(sessionmaker)
    future = datetime(2031, 5, 10, 12, 0, tzinfo=UTC)
    async with sessionmaker() as s:
        s.add(
            Reading(
                reseller_id=reseller_id, device_id=device_id, read_at=future, idempotency_key="fut", total=7
            )
        )
        s.add(ReadingIdempotency(idempotency_key="fut", reading_id=uuid.uuid4(), read_at=future))
        await s.commit()
    async with sessionmaker() as s:
        assert (await default_partition_rows(s))["readings"] == 1
        created = await ensure_partitions(s, months_back=0, months_ahead=0, today=date(2031, 5, 1))
        await s.commit()
    assert "readings_2031_05" in created
    async with sessionmaker() as s:
        assert (await default_partition_rows(s))["readings"] == 0
        in_part = (await s.execute(text("SELECT total FROM readings_2031_05"))).scalar_one()
        assert in_part == 7
        again = await ensure_partitions(s, months_back=0, months_ahead=0, today=date(2031, 5, 1))
        assert again == []
        # A nova partição herdou a proteção contra TRUNCATE.
        with pytest.raises(DBAPIError, match="somente inserção"):
            await s.execute(text("TRUNCATE readings_2031_05 CASCADE"))
    async with sessionmaker() as s:
        await s.execute(text("SET dati.maintenance = 'on'"))
        for table in ("readings", "supply_readings", "agent_heartbeats"):
            await s.execute(text(f"ALTER TABLE {table} DETACH PARTITION {table}_2031_05"))
            await s.execute(text(f"DROP TABLE {table}_2031_05"))
        await s.commit()


async def test_bootstrap_creates_admin_once_and_seed_is_idempotent(
    sessionmaker: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with sessionmaker() as s:
        first = await ensure_bootstrap(s, test_settings)
        await s.commit()
    assert first.created_admin
    assert first.admin_password
    async with sessionmaker() as s:
        second = await ensure_bootstrap(s, test_settings)
        await s.commit()
    assert not second.created_admin
    async with sessionmaker() as s:
        admin = (await s.execute(select(User))).scalar_one()
        assert admin.email == "admin@local"
        assert admin.role_code == "superadmin"
        assert admin.must_change_password
        assert (await s.execute(select(Reseller.name))).scalar_one() == test_settings.bootstrap_reseller_name
        r1 = await seed_dev(s, test_settings)
        r2 = await seed_dev(s, test_settings)
        await s.commit()
    assert r1.created
    assert not r2.created
    async with sessionmaker() as s:
        assert (await s.execute(select(func.count()).select_from(Company))).scalar_one() == 1
        assert (await s.execute(select(func.count()).select_from(Customer))).scalar_one() == 2
        assert (await s.execute(select(func.count()).select_from(Site))).scalar_one() == 2
    from app.services.bootstrap import announce_bootstrap  # noqa: PLC0415

    announce_bootstrap(first)
    assert first.admin_password in capsys.readouterr().err


async def test_sync_roles_mirrors_permission_matrix(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as s:
        s.add(RolePermission(role_code="operator", permission="permissao.obsoleta"))
        await s.commit()
    async with sessionmaker() as s:
        await sync_roles(s)
        await s.commit()
        perms = set(
            (
                await s.execute(
                    select(RolePermission.permission).where(RolePermission.role_code == "operator")
                )
            ).scalars()
        )
    assert "permissao.obsoleta" not in perms
    assert "customers.write" in perms
    assert "users.write" not in perms
