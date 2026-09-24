"""Users, roles, per-action permissions and authentication tokens."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, LargeBinary, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSONB_EMPTY_OBJECT, Base, CreatedMixin, IdMixin, SoftDeleteMixin, TimestampMixin


class Role(Base, TimestampMixin):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    level: Mapped[int] = mapped_column(Integer)


class RolePermission(Base, CreatedMixin):
    __tablename__ = "role_permissions"

    role_code: Mapped[str] = mapped_column(ForeignKey("roles.code", ondelete="CASCADE"), primary_key=True)
    permission: Mapped[str] = mapped_column(String(64), primary_key=True)


class User(Base, IdMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email_active", "email", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )

    reseller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resellers.id"), index=True)
    # Escopo opcional por cliente: o usuário só enxerga o parque deste cliente.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))  # sempre em minúsculas
    password_hash: Mapped[str] = mapped_column(String(255))
    role_code: Mapped[str] = mapped_column(ForeignKey("roles.code"))
    totp_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    # Último passo TOTP aceito: impede reutilizar o mesmo código (replay).
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)
    failed_login_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    locked_until: Mapped[datetime | None] = mapped_column(default=None)
    must_change_password: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(default=None)
    # Preferências da interface (colunas da tela de parque, tema...).
    preferences: Mapped[dict[str, Any]] = mapped_column(server_default=JSONB_EMPTY_OBJECT, default=dict)


class RefreshToken(Base, IdMixin, CreatedMixin):
    """Refresh token rotativo. Reuso de um token já trocado revoga toda a família."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    created_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class PasswordResetToken(Base, IdMixin, CreatedMixin):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None] = mapped_column(default=None)
