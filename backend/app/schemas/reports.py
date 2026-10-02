"""Schemas of the Relatórios screen (PROMPT 10.9 / 16.12)."""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class ReportOption(BaseModel):
    value: str
    label: str


class ReportInfo(BaseModel):
    key: str
    title: str
    group: str
    description: str
    period: bool = Field(description="Usa o filtro de período (de/até)")
    cutoff_date: bool = Field(description="Usa uma data de corte (campo 'até')")
    month: bool
    hours: bool
    default_days: int
    date_types: list[ReportOption]
    group_by: list[ReportOption]


class ReportColumn(BaseModel):
    key: str
    label: str
    kind: Literal["text", "int", "decimal", "money", "percent", "datetime", "date", "bool"]


class ReportChart(BaseModel):
    kind: Literal["bar", "line"]
    x: str
    series: list[ReportOption] = Field(description="value = chave da coluna; label = legenda")
    stacked: bool


class ReportAppliedFilters(BaseModel):
    date_from: date | None
    date_to: date | None
    date_type: str | None
    group_by: str | None
    month: str | None
    hours: int | None


class ReportResult(BaseModel):
    key: str
    title: str
    filters: ReportAppliedFilters
    columns: list[ReportColumn]
    rows: list[dict[str, Any]]
    total_rows: int
    offset: int
    limit: int
    totals: dict[str, Any] | None
    chart: ReportChart | None
    chart_rows: list[dict[str, Any]] | None = Field(description="Todas as linhas do gráfico (não paginadas)")
    notes: list[str]
