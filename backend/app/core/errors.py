"""Application errors with stable codes and Portuguese messages, rendered as JSON."""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError

from app.core.responses import UTF8JSONResponse as JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra


def not_found(entity: str) -> AppError:
    return AppError(status.HTTP_404_NOT_FOUND, "not_found", f"{entity} não encontrado(a)")


def forbidden(message: str = "Você não tem permissão para esta ação") -> AppError:
    return AppError(status.HTTP_403_FORBIDDEN, "forbidden", message)


def conflict(code: str, message: str) -> AppError:
    return AppError(status.HTTP_409_CONFLICT, code, message)


def bad_request(code: str, message: str, **extra: Any) -> AppError:
    return AppError(status.HTTP_400_BAD_REQUEST, code, message, **extra)


def unauthorized(code: str = "unauthorized", message: str = "Autenticação necessária") -> AppError:
    return AppError(status.HTTP_401_UNAUTHORIZED, code, message)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == status.HTTP_401_UNAUTHORIZED else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": {"code": exc.code, "message": exc.message, **exc.extra}},
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": {"code": "validation_error", "message": "Dados inválidos", "errors": errors}},
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("erro não tratado em %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": {"code": "internal_error", "message": "Erro interno. O incidente foi registrado."}
            },
        )
