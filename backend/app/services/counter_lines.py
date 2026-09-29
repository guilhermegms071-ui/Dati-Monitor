"""Counters as rows (PROMPT 16.2): maps each counter of a reading to (kind, color_mode, size).

The normalized names produced by the profiles have a default mapping here; a profile can override it or
map any other counter with `line: {kind, color_mode, size}` (sent by the agent in `counter_lines`).
Counters without an unambiguous meaning (Canon "small", "Total 2"…) stay only in `readings.extra`.
"""

from dataclasses import dataclass

from app.models.readings import COUNTER_COLOR_MODES, COUNTER_KINDS, COUNTER_SIZES


@dataclass(frozen=True)
class Line:
    kind: str
    color_mode: str
    size: str


# `color` é "páginas com cor" (na Canon inclui monocor): vai para full_color; o perfil pode sobrescrever.
DEFAULT_LINES: dict[str, Line] = {
    "total": Line("total", "any", "any"),
    "mono": Line("total", "mono", "any"),
    "color": Line("total", "full_color", "any"),
    "mono_large": Line("total", "mono", "a3"),
    "color_large": Line("total", "full_color", "a3"),
    "copy_total": Line("copy", "any", "any"),
    "copy_mono": Line("copy", "mono", "any"),
    "copy_color": Line("copy", "full_color", "any"),
    "print_total": Line("print", "any", "any"),
    "print_mono": Line("print", "mono", "any"),
    "print_color": Line("print", "full_color", "any"),
    "scan": Line("scan", "any", "any"),
    "fax": Line("fax", "any", "any"),
    "duplex": Line("duplex", "any", "any"),
}


def valid_line(kind: str, color_mode: str, size: str) -> bool:
    return kind in COUNTER_KINDS and color_mode in COUNTER_COLOR_MODES and size in COUNTER_SIZES


def resolve_lines(
    counters: dict[str, int], overrides: dict[str, Line] | None = None
) -> tuple[list[tuple[str, Line, int]], list[str]]:
    """Returns (name, line, value) per mapped counter and the names that collided on the same line.

    Profile mappings win over defaults; two counters on the same line keep the first in name order
    (deterministic) and the others are reported so the profile can be fixed.
    """
    overrides = overrides or {}
    taken: dict[Line, str] = {}
    out: list[tuple[str, Line, int]] = []
    conflicts: list[str] = []
    # Mapeamentos explícitos do perfil primeiro: um contador novo mapeado para uma linha padrão vence.
    names = sorted(counters, key=lambda n: (n not in overrides, n))
    for name in names:
        line = overrides.get(name) or DEFAULT_LINES.get(name)
        if line is None:
            continue
        if line in taken:
            conflicts.append(name)
            continue
        taken[line] = name
        out.append((name, line, int(counters[name])))
    return out, conflicts
