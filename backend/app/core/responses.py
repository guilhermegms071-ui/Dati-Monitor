"""JSON responses that always declare charset=utf-8 (some clients, e.g. PowerShell 5.1, assume Latin-1)."""

from fastapi.responses import JSONResponse


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"
