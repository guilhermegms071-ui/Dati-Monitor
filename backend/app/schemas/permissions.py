"""Schemas of the permission matrix (PROMPT 16.14)."""

import uuid

from pydantic import BaseModel, Field


class MatrixModule(BaseModel):
    code: str
    name: str


class RoleMatrix(BaseModel):
    role: str
    role_name: str
    permissions: list[str] = Field(description="Permissões da matriz que o papel tem nesta revenda")
    grantable: list[str] = Field(description="Permissões da matriz que este papel pode receber")
    customized: bool = Field(description="A revenda ajustou este papel (senão vale o padrão do sistema)")


class PermissionMatrix(BaseModel):
    reseller_id: uuid.UUID
    modules: list[MatrixModule]
    actions: dict[str, str] = Field(
        description="read/create/update/delete → Consultar/Incluir/Alterar/Excluir"
    )
    supplies_permission: str
    roles: list[RoleMatrix]


class RoleMatrixIn(BaseModel):
    permissions: list[str] | None = Field(
        description="Permissões da matriz; null volta ao padrão do sistema", max_length=64
    )
