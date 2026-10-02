"""/api/v1 Computadores (PROMPT 11): PCs com coletor, impressoras USB e leitura manual."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import PrincipalDep, SessionDep
from app.schemas.common import ERROR_RESPONSES
from app.schemas.computers import ComputerPage, ManualReadingIn, ManualReadingOut, UsbPrinterOut
from app.services import computers as svc
from app.services.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

router = APIRouter(responses=ERROR_RESPONSES, tags=["computadores"])


@router.get("/computers", response_model=ComputerPage, summary="PCs com coletor (e impressoras USB)")
async def list_computers(
    p: PrincipalDep,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> ComputerPage:
    return await svc.list_computers(session, p, q=q, cursor=cursor, limit=limit)


@router.get(
    "/computers/{agent_id}/usb-printers", response_model=list[UsbPrinterOut], summary="Impressoras USB do PC"
)
async def usb_printers(agent_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> list[UsbPrinterOut]:
    return await svc.usb_printers(session, p, agent_id)


@router.post(
    "/devices/{device_id}/manual-readings",
    response_model=ManualReadingOut,
    status_code=status.HTTP_201_CREATED,
    summary="Leitura manual (folha de contadores)",
)
async def manual_reading(
    device_id: uuid.UUID, body: ManualReadingIn, p: PrincipalDep, session: SessionDep
) -> ManualReadingOut:
    r = await svc.add_manual_reading(session, p, device_id, body)
    await session.commit()
    return ManualReadingOut(reading_id=r.id, read_at=r.read_at, total=r.total)
