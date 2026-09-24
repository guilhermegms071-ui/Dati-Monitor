"""Backend version, read from the installed package metadata."""

from importlib.metadata import PackageNotFoundError, version


def backend_version() -> str:
    try:
        return version("dati-monitor-backend")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "Pacote dati-monitor-backend não instalado no venv; rode: pip install -e backend[dev]"
        ) from exc
