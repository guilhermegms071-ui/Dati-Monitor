"""/api/v1 Perfis de modelos (PROMPT 6.6): read profile versions, YAML validation and publishing,
activating an older version, the walk explorer and exporting a walk as a test recording."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from app.api.deps import PrincipalDep, SessionDep
from app.schemas.common import ERROR_RESPONSES
from app.schemas.profiles import (
    ActivateIn,
    FixtureIn,
    FixtureOut,
    ProfileDetail,
    ProfileSummary,
    ProfileText,
    ProfileValidation,
    WalkTree,
)
from app.services import profiles as svc

router = APIRouter(responses=ERROR_RESPONSES, tags=["perfis"])


@router.get("/profiles", response_model=list[ProfileSummary], summary="Perfis de leitura (versão ativa)")
async def list_profiles(p: PrincipalDep, session: SessionDep) -> list[ProfileSummary]:
    return await svc.list_profiles(session, p)


@router.get("/profiles/{key}", response_model=ProfileDetail, summary="Perfil com o histórico de versões")
async def get_profile(key: str, p: PrincipalDep, session: SessionDep) -> ProfileDetail:
    return await svc.get_profile(session, p, key)


@router.get(
    "/profiles/{key}/versions/{version}",
    response_class=PlainTextResponse,
    summary="YAML de uma versão",
)
async def get_version(key: str, version: int, p: PrincipalDep, session: SessionDep) -> str:
    return await svc.get_version_yaml(session, p, key, version)


@router.post("/profiles/validate", response_model=ProfileValidation, summary="Valida o YAML sem publicar")
async def validate_profile(body: ProfileText, p: PrincipalDep) -> ProfileValidation:
    p.require("profiles.read")
    return svc.validate(body.yaml)


@router.post("/profiles", response_model=ProfileDetail, summary="Publica nova versão (vai para os coletores)")
async def publish_profile(body: ProfileText, p: PrincipalDep, session: SessionDep) -> ProfileDetail:
    out = await svc.publish(session, p, body.yaml, body.notes)
    await session.commit()
    return out


@router.post(
    "/profiles/{key}/activate", response_model=ProfileDetail, summary="Ativa uma versão do histórico"
)
async def activate_profile(key: str, body: ActivateIn, p: PrincipalDep, session: SessionDep) -> ProfileDetail:
    out = await svc.activate(session, p, key, body.version)
    await session.commit()
    return out


@router.get("/mib-walks/{walk_id}/tree", response_model=WalkTree, summary="Explorador do walk")
async def walk_tree(
    walk_id: uuid.UUID,
    p: PrincipalDep,
    session: SessionDep,
    value: Annotated[
        str | None, Query(max_length=200, description="Valor exato (ex.: contador da folha)")
    ] = None,
    q: Annotated[str | None, Query(max_length=200, description="Texto no OID ou no valor")] = None,
    prefix: Annotated[str | None, Query(max_length=255, description="Sub-árvore (OID)")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=svc.MAX_WALK_ROWS)] = 200,
) -> WalkTree:
    return await svc.walk_tree(
        session, p, walk_id, value=value, q=q, prefix=prefix, offset=offset, limit=limit
    )


@router.post(
    "/mib-walks/{walk_id}/fixture", response_model=FixtureOut, summary="Salva como gravação de teste"
)
async def save_fixture(
    walk_id: uuid.UUID, body: FixtureIn, p: PrincipalDep, session: SessionDep
) -> FixtureOut:
    name = await svc.save_fixture(session, p, walk_id, body)
    await session.commit()
    return FixtureOut(file=name)
