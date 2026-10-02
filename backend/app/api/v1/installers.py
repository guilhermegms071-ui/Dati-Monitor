"""/api/v1 Downloads (Fase 8): instaladores publicados e links de instalação do coletor;
/api/public: download do instalador e install.sh com o código de cadastro (sem login)."""

import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import FileResponse, PlainTextResponse

from app.api.deps import PrincipalDep, SessionDep, SettingsDep, client_ip
from app.core.errors import AppError
from app.core.ratelimit import RateLimiter
from app.schemas.common import ERROR_RESPONSES
from app.schemas.installers import InstallerOut, InstallerUpdate
from app.services import installers as svc

router = APIRouter(responses=ERROR_RESPONSES, tags=["downloads"])
public_router = APIRouter(prefix="/api/public", responses=ERROR_RESPONSES, tags=["downloads"])
Code = Annotated[str, Query(min_length=8, max_length=9, pattern=r"^[A-Za-z0-9-]{8,9}$")]


def _file(path: str, filename: str) -> FileResponse:
    return FileResponse(Path(path), filename=filename, media_type="application/octet-stream")


@router.get("/installers", response_model=list[InstallerOut], summary="Instaladores publicados")
async def list_installers(p: PrincipalDep, session: SessionDep) -> list[InstallerOut]:
    return await svc.list_installers(session, p)


@router.post(
    "/installers",
    response_model=InstallerOut,
    status_code=status.HTTP_201_CREATED,
    summary="Publicar instalador (arquivo no corpo)",
    openapi_extra={"requestBody": {"content": {"application/octet-stream": {}}, "required": True}},
)
async def publish_installer(
    request: Request,
    p: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    kind: Annotated[Literal["windows", "deb", "tar"], Query()],
    arch: Annotated[str, Query(pattern="^(all|amd64|386|arm64|arm)$")],
    version: Annotated[str, Query(min_length=1, max_length=64)],
    filename: Annotated[str, Query(min_length=3, max_length=200)],
    notes: Annotated[str | None, Query(max_length=4000)] = None,
) -> InstallerOut:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > svc.MAX_INSTALLER_BYTES:
            raise AppError(status.HTTP_413_CONTENT_TOO_LARGE, "file_too_large", "Arquivo acima de 200 MB")
    out = await svc.publish(
        session, settings, p, kind=kind, arch=arch, version=version, filename=filename, notes=notes,
        data=bytes(data),
    )  # fmt: skip
    await session.commit()
    return out


@router.patch(
    "/installers/{installer_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Retirar/devolver"
)
async def update_installer(
    installer_id: uuid.UUID, body: InstallerUpdate, p: PrincipalDep, session: SessionDep
) -> Response:
    await svc.set_withdrawn(session, p, installer_id, body.withdrawn)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/installers/{installer_id}/file",
    response_class=FileResponse,
    summary="Baixar instalador",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_installer(installer_id: uuid.UUID, p: PrincipalDep, session: SessionDep) -> FileResponse:
    row = await svc.get_for_download(session, p, installer_id)
    return _file(row.file_path, row.filename)


# ----------------------------------------------------------------------------- sem login


def _rate(request: Request) -> None:
    limiter: RateLimiter = request.app.state.agent_limiter
    if not limiter.hit(f"public-installer:{client_ip(request)}"):
        raise AppError(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited", "Muitas requisições; aguarde")


@public_router.get(
    "/installer",
    response_class=FileResponse,
    summary="Baixar o instalador com o código de cadastro (sem login)",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def public_installer(
    request: Request,
    session: SessionDep,
    code: Code,
    platform: Annotated[Literal["windows", "linux"], Query()] = "windows",
    arch: Annotated[Literal["amd64", "386", "arm64", "arm"] | None, Query()] = None,
    format: Annotated[Literal["deb", "tar"] | None, Query()] = None,  # noqa: A002 - nome na URL
) -> FileResponse:
    _rate(request)
    row = await svc.public_installer(
        session, code=code.replace("-", ""), platform=platform, arch=arch, fmt=format
    )
    return _file(row.file_path, row.filename)


@public_router.get(
    "/install.sh",
    response_class=PlainTextResponse,
    summary="Script de instalação Linux com servidor e código preenchidos (sem login)",
)
async def public_install_sh(request: Request, session: SessionDep, settings: SettingsDep, code: Code) -> str:
    _rate(request)
    return await svc.install_script(session, settings, code.replace("-", ""))
