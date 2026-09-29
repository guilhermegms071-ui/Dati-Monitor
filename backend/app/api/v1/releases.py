"""/api/v1/releases — signed releases of the collector and watchdog (PROMPT 5.2), and the cluster's
preferred MASTER (4.8)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from app.api.deps import PrincipalDep, SessionDep, SettingsDep
from app.core.errors import AppError
from app.models import AgentRelease
from app.schemas.common import ERROR_RESPONSES
from app.schemas.releases import PreferredMasterIn, ReleaseOut, ReleaseUpdate
from app.services import agent_ops as ops
from app.services import releases as svc

router = APIRouter(responses=ERROR_RESPONSES, tags=["versões"])


async def releases_out(
    session: SessionDep, settings: SettingsDep, rows: list[AgentRelease]
) -> list[ReleaseOut]:
    stats = await svc.release_stats(session, [r.id for r in rows])
    out = []
    for r in rows:
        s = stats[r.id]
        out.append(
            ReleaseOut.model_validate(r).model_copy(
                update={
                    "updates_succeeded": s.succeeded,
                    "updates_failed": s.failed,
                    "updates_in_progress": s.in_progress,
                    "canary_failure_percent": round(s.canary_failure_percent, 1),
                    "auto_update_blocked": s.canary_failure_percent
                    > settings.update_max_canary_failure_percent,
                }
            )
        )
    return out


@router.get("/releases", response_model=list[ReleaseOut], summary="Versões publicadas (com taxa de falha)")
async def list_releases(p: PrincipalDep, session: SessionDep, settings: SettingsDep) -> list[ReleaseOut]:
    return await releases_out(session, settings, await svc.list_releases(session, p))


@router.post(
    "/releases",
    response_model=ReleaseOut,
    status_code=status.HTTP_201_CREATED,
    summary="Publicar versão (binário no corpo; assinatura ed25519 feita com dm-tool sign)",
    openapi_extra={"requestBody": {"content": {"application/octet-stream": {}}, "required": True}},
)
async def publish_release(
    request: Request,
    p: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    component: Annotated[str, Query(pattern="^(agent|watchdog)$")],
    version: Annotated[str, Query(min_length=1, max_length=64)],
    os: Annotated[str, Query(pattern="^(windows|linux)$")],
    arch: Annotated[str, Query(pattern="^(amd64|386|arm64|arm)$")],
    signature: Annotated[str, Query(min_length=20, max_length=200)],
    channel: Annotated[str, Query(pattern="^(canary|stable)$")] = "canary",
    rollout_percent: Annotated[int, Query(ge=0, le=100)] = 100,
    notes: Annotated[str | None, Query(max_length=4000)] = None,
) -> ReleaseOut:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > svc.MAX_RELEASE_BYTES:
            raise AppError(status.HTTP_413_CONTENT_TOO_LARGE, "file_too_large", "Arquivo acima de 80 MB")
    release = await svc.publish(
        session,
        settings,
        p,
        svc.PublishInput(
            component=component,
            version=version,
            os=os,
            arch=arch,
            channel=channel,
            rollout_percent=rollout_percent,
            notes=notes,
            signature=signature,
        ),
        bytes(data),
    )
    await session.commit()
    return (await releases_out(session, settings, [release]))[0]


@router.patch(
    "/releases/{release_id}", response_model=ReleaseOut, summary="Canal, liberação gradual, retirar"
)
async def update_release(
    release_id: uuid.UUID, body: ReleaseUpdate, p: PrincipalDep, session: SessionDep, settings: SettingsDep
) -> ReleaseOut:
    release = await svc.update_release(session, p, release_id, body.model_dump(exclude_unset=True))
    await session.commit()
    return (await releases_out(session, settings, [release]))[0]


@router.put(
    "/sites/{site_id}/preferred-master",
    response_model=ops.SiteCluster,
    summary="Fixar o MASTER preferido do local (assume assim que estiver online)",
)
async def set_preferred_master(
    site_id: uuid.UUID, body: PreferredMasterIn, p: PrincipalDep, session: SessionDep
) -> ops.SiteCluster:
    await ops.set_preferred_master(session, p, site_id, body.agent_id)
    await session.commit()
    return await ops.cluster(session, p, site_id)
