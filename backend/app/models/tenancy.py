"""Hierarchy: Revenda (reseller) → Empresa (company) → Cliente (customer) → Local (site)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSONB_EMPTY_OBJECT, Base, IdMixin, SoftDeleteMixin, TimestampMixin

TONER_COLORS = ("black", "cyan", "magenta", "yellow")
DEFAULT_TONER_THRESHOLDS: dict[str, int] = dict.fromkeys(TONER_COLORS, 10)
DEFAULT_TONER_THRESHOLDS_SQL = '\'{"black": 10, "cyan": 10, "magenta": 10, "yellow": 10}\'::jsonb'


class Reseller(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "resellers"

    name: Mapped[str] = mapped_column(String(200))
    cnpj: Mapped[str | None] = mapped_column(String(14))
    logo_url: Mapped[str | None] = mapped_column(Text)


class Company(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "companies"

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    legal_name: Mapped[str] = mapped_column(String(200))
    cnpj: Mapped[str | None] = mapped_column(String(14))


class Customer(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "customers"
    __table_args__ = (
        Index(
            "uq_customers_reseller_erp_code",
            "reseller_id",
            "erp_code",
            unique=True,
            postgresql_where=text("erp_code IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    cnpj: Mapped[str | None] = mapped_column(String(14))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(320))
    erp_code: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    # Limiar de toner (seção 16.5): liga/desliga e um limiar por cor, em %.
    toner_monitoring: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    toner_thresholds: Mapped[dict[str, Any]] = mapped_column(
        server_default=text(DEFAULT_TONER_THRESHOLDS_SQL), default=lambda: dict(DEFAULT_TONER_THRESHOLDS)
    )


class Site(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "sites"

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # Endereço completo (seção 16.9); o CEP preenche o resto pelo ViaCEP no portal.
    cep: Mapped[str | None] = mapped_column(String(8))
    street: Mapped[str | None] = mapped_column(String(300))
    number: Mapped[str | None] = mapped_column(String(30))
    complement: Mapped[str | None] = mapped_column(String(200))
    district: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(200))
    state: Mapped[str | None] = mapped_column(String(2))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    # Descobertas (seção 16.1): equipamento novo entra ativo sem passar pela tela de Descobertas.
    auto_activate_devices: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    timezone: Mapped[str] = mapped_column(
        String(64), server_default=text("'America/Sao_Paulo'"), default="America/Sao_Paulo"
    )
    # Intervalos de coleta, limites etc. (seção 4.6); vazio = padrões do sistema.
    collection_config: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)
    # Cluster (seção 4.8): o servidor é a autoridade do lease do MASTER.
    master_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", use_alter=True, name="fk_sites_master_agent_id_agents")
    )
    preferred_master_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", use_alter=True, name="fk_sites_preferred_master_agent_id_agents")
    )
    master_lease_expires_at: Mapped[datetime | None] = mapped_column(default=None)
