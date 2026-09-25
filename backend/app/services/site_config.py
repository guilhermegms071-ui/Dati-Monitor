"""IP ranges and SNMP credentials of a site (PROMPT 3/4.5). Secrets are AES-GCM encrypted and never
returned by the API; every change bumps the configuration version of the site's agents."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import Settings
from app.core.errors import bad_request, not_found
from app.core.principal import Principal
from app.models import IpRange, Site, SnmpCredential
from app.schemas.collection import IpRangeIn, SnmpCredentialIn, SnmpCredentialOut, SnmpCredentialUpdate
from app.services import audit
from app.services.agents import bump_site_config, snmp_aad

DEFAULT_COMMUNITY = "public"


async def _site(session: AsyncSession, p: Principal, site_id: uuid.UUID, *, write: bool) -> Site:
    p.require("sites.write" if write else "sites.read")
    site = await session.get(Site, site_id)
    if (
        site is None
        or site.deleted_at is not None
        or not p.can_access_customer(site.reseller_id, site.customer_id)
    ):
        raise not_found("Local")
    return site


# ----------------------------------------------------------------------------- IP ranges


async def list_ranges(session: AsyncSession, p: Principal, site_id: uuid.UUID) -> list[IpRange]:
    await _site(session, p, site_id, write=False)
    rows = await session.execute(
        select(IpRange).where(IpRange.site_id == site_id).order_by(IpRange.created_at)
    )
    return list(rows.scalars())


async def create_range(session: AsyncSession, p: Principal, site_id: uuid.UUID, data: IpRangeIn) -> IpRange:
    site = await _site(session, p, site_id, write=True)
    row = IpRange(reseller_id=site.reseller_id, site_id=site.id, status="approved", **data.model_dump())
    session.add(row)
    await session.flush()
    await bump_site_config(session, site.id)
    await audit.record(
        session,
        p,
        action="create",
        entity="ip_range",
        entity_id=row.id,
        reseller_id=site.reseller_id,
        after=audit.snapshot(row),
    )
    return row


async def _range(session: AsyncSession, p: Principal, range_id: uuid.UUID) -> tuple[IpRange, Site]:
    row = await session.get(IpRange, range_id)
    if row is None:
        raise not_found("Faixa de IP")
    site = await _site(session, p, row.site_id, write=True)
    return row, site


async def update_range(session: AsyncSession, p: Principal, range_id: uuid.UUID, data: IpRangeIn) -> IpRange:
    row, site = await _range(session, p, range_id)
    before = audit.snapshot(row)
    for k, v in data.model_dump().items():
        setattr(row, k, v)
    await session.flush()
    await bump_site_config(session, site.id)
    b, a = audit.diff(before, audit.snapshot(row))
    await audit.record(
        session,
        p,
        action="update",
        entity="ip_range",
        entity_id=row.id,
        reseller_id=site.reseller_id,
        before=b,
        after=a,
    )
    return row


async def approve_range(session: AsyncSession, p: Principal, range_id: uuid.UUID) -> IpRange:
    row, site = await _range(session, p, range_id)
    if row.status == "approved":
        raise bad_request("already_approved", "A faixa já está aprovada")
    row.status, row.active = "approved", True
    await bump_site_config(session, site.id)
    await audit.record(
        session,
        p,
        action="approve",
        entity="ip_range",
        entity_id=row.id,
        reseller_id=site.reseller_id,
        after={"cidr": row.cidr},
    )
    return row


async def delete_range(session: AsyncSession, p: Principal, range_id: uuid.UUID) -> None:
    row, site = await _range(session, p, range_id)
    snap = audit.snapshot(row)
    await session.delete(row)
    await bump_site_config(session, site.id)
    await audit.record(
        session,
        p,
        action="delete",
        entity="ip_range",
        entity_id=range_id,
        reseller_id=site.reseller_id,
        before=snap,
    )


# ----------------------------------------------------------------------------- SNMP credentials


def credential_out(c: SnmpCredential, settings: Settings) -> SnmpCredentialOut:
    hint = None
    if c.community_enc:
        community = crypto.decrypt(
            settings.master_key_bytes, c.community_enc, associated_data=snmp_aad(c.site_id)
        )
        hint = f"{community[0]}…{community[-1]}" if len(community) > 2 else "…"  # noqa: PLR2004
    return SnmpCredentialOut(
        id=c.id,
        site_id=c.site_id,
        position=c.position,
        version=c.version,
        has_community=c.community_enc is not None,
        community_hint=hint,
        v3_username=c.v3_username,
        v3_auth_protocol=c.v3_auth_protocol,
        v3_priv_protocol=c.v3_priv_protocol,
        has_auth_password=c.v3_auth_password_enc is not None,
        has_priv_password=c.v3_priv_password_enc is not None,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _enc(settings: Settings, site_id: uuid.UUID, value: str | None) -> bytes | None:
    return (
        crypto.encrypt(settings.master_key_bytes, value, associated_data=snmp_aad(site_id)) if value else None
    )


async def list_credentials(session: AsyncSession, p: Principal, site_id: uuid.UUID) -> list[SnmpCredential]:
    await _site(session, p, site_id, write=False)
    rows = await session.execute(
        select(SnmpCredential).where(SnmpCredential.site_id == site_id).order_by(SnmpCredential.position)
    )
    return list(rows.scalars())


async def create_credential(
    session: AsyncSession, settings: Settings, p: Principal, site_id: uuid.UUID, data: SnmpCredentialIn
) -> SnmpCredential:
    site = await _site(session, p, site_id, write=True)
    position = data.position
    if position is None:
        current = (
            await session.execute(
                select(func.max(SnmpCredential.position)).where(SnmpCredential.site_id == site.id)
            )
        ).scalar_one()
        position = (current or 0) + 1
    taken = (
        await session.execute(
            select(func.count())
            .select_from(SnmpCredential)
            .where(SnmpCredential.site_id == site.id, SnmpCredential.position == position)
        )
    ).scalar_one()
    if taken:
        raise bad_request("position_taken", f"Já existe uma credencial na posição {position}")
    row = SnmpCredential(
        reseller_id=site.reseller_id,
        site_id=site.id,
        position=position,
        version=data.version,
        community_enc=_enc(settings, site.id, data.community),
        v3_username=data.v3_username,
        v3_auth_protocol=data.v3_auth_protocol,
        v3_auth_password_enc=_enc(settings, site.id, data.v3_auth_password),
        v3_priv_protocol=data.v3_priv_protocol,
        v3_priv_password_enc=_enc(settings, site.id, data.v3_priv_password),
    )
    session.add(row)
    await session.flush()
    await bump_site_config(session, site.id)
    await audit.record(
        session,
        p,
        action="create",
        entity="snmp_credential",
        entity_id=row.id,
        reseller_id=site.reseller_id,
        after={"position": position, "version": data.version, "v3_username": data.v3_username},
    )
    return row


async def update_credential(
    session: AsyncSession, settings: Settings, p: Principal, cred_id: uuid.UUID, data: SnmpCredentialUpdate
) -> SnmpCredential:
    row = await session.get(SnmpCredential, cred_id)
    if row is None:
        raise not_found("Credencial SNMP")
    site = await _site(session, p, row.site_id, write=True)
    changes = data.model_dump(exclude_unset=True)
    if "position" in changes and changes["position"] is not None:
        row.position = changes["position"]
    if changes.get("community"):
        row.community_enc = _enc(settings, site.id, changes["community"])
    if "v3_username" in changes:
        row.v3_username = changes["v3_username"]
    if "v3_auth_protocol" in changes:
        row.v3_auth_protocol = changes["v3_auth_protocol"]
    if changes.get("v3_auth_password"):
        row.v3_auth_password_enc = _enc(settings, site.id, changes["v3_auth_password"])
    if "v3_priv_protocol" in changes:
        row.v3_priv_protocol = changes["v3_priv_protocol"]
    if changes.get("v3_priv_password"):
        row.v3_priv_password_enc = _enc(settings, site.id, changes["v3_priv_password"])
    await session.flush()
    await bump_site_config(session, site.id)
    await audit.record(
        session,
        p,
        action="update",
        entity="snmp_credential",
        entity_id=row.id,
        reseller_id=site.reseller_id,
        after={k: ("***" if "password" in k or k == "community" else v) for k, v in changes.items()},
    )
    return row


async def delete_credential(session: AsyncSession, p: Principal, cred_id: uuid.UUID) -> None:
    row = await session.get(SnmpCredential, cred_id)
    if row is None:
        raise not_found("Credencial SNMP")
    site = await _site(session, p, row.site_id, write=True)
    await session.delete(row)
    await bump_site_config(session, site.id)
    await audit.record(
        session, p, action="delete", entity="snmp_credential", entity_id=cred_id, reseller_id=site.reseller_id
    )


async def add_default_credential(session: AsyncSession, settings: Settings, site: Site) -> None:
    """New sites start with SNMPv2c community "public" (the factory default of most printers)."""
    session.add(
        SnmpCredential(
            reseller_id=site.reseller_id,
            site_id=site.id,
            position=1,
            version="v2c",
            community_enc=_enc(settings, site.id, DEFAULT_COMMUNITY),
        )
    )
