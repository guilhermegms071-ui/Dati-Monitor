"""Histórico de cliente/local de cada equipamento (device_assignments): quem muda o local de um equipamento
chama `move`, para os relatórios de cada cliente contarem só o período em que o equipamento esteve com ele."""

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, DeviceAssignment, Site

# O primeiro vínculo vale "desde sempre": leituras manuais com data antiga continuam do cliente original.
SINCE_ALWAYS = datetime(1970, 1, 1, tzinfo=UTC)


def first(device: Device) -> DeviceAssignment:
    """Vínculo inicial de um equipamento novo (chamar depois do flush que gera o id)."""
    return DeviceAssignment(
        reseller_id=device.reseller_id,
        device_id=device.id,
        customer_id=device.customer_id,
        site_id=device.site_id,
        start_at=SINCE_ALWAYS,
    )


async def move(session: AsyncSession, device: Device, site: Site, at: datetime) -> None:
    """Fecha o vínculo atual em `at`, abre um no novo local e atualiza o equipamento."""
    await session.execute(
        update(DeviceAssignment)
        .where(DeviceAssignment.device_id == device.id, DeviceAssignment.end_at.is_(None))
        .values(end_at=at)
    )
    session.add(
        DeviceAssignment(
            reseller_id=device.reseller_id,
            device_id=device.id,
            customer_id=site.customer_id,
            site_id=site.id,
            start_at=at,
        )
    )
    device.site_id, device.customer_id = site.id, site.customer_id
