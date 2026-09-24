"""Schemas shared by every API module."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    model_config = ConfigDict(extra="allow")


class ErrorResponse(BaseModel):
    detail: ErrorDetail


class OkResponse(BaseModel):
    ok: Literal[True] = True


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Requisição inválida"},
    401: {"model": ErrorResponse, "description": "Não autenticado"},
    403: {"model": ErrorResponse, "description": "Sem permissão"},
    404: {"model": ErrorResponse, "description": "Não encontrado"},
    409: {"model": ErrorResponse, "description": "Conflito"},
}
