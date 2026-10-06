"""Relatórios (PROMPT 10.9 e 16.12). Importar o pacote registra todas as definições."""

from app.services.reports import definitions as _definitions  # noqa: F401 - registra os relatórios
from app.services.reports.base import (
    REGISTRY,
    SECTION_KEY,
    Filters,
    ReportData,
    ReportDef,
    Section,
    get,
    normalize,
)

__all__ = ["REGISTRY", "SECTION_KEY", "Filters", "ReportData", "ReportDef", "Section", "get", "normalize"]
