"""Product identity loaded from the repository-level product.json (single source of truth)."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PRODUCT_FILE = REPO_ROOT / "product.json"


@dataclass(frozen=True)
class Product:
    name: str
    slug: str
    service_prefix: str

    @property
    def agent_service_name(self) -> str:
        return f"{self.service_prefix}Agent"

    @property
    def watchdog_service_name(self) -> str:
        return f"{self.service_prefix}Watchdog"


def load_product(path: Path = PRODUCT_FILE) -> Product:
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in ("name", "slug", "service_prefix") if not data.get(k)]
    if missing:
        raise ValueError(f"{path}: campos obrigatórios ausentes: {', '.join(missing)}")
    return Product(name=data["name"], slug=data["slug"], service_prefix=data["service_prefix"])


@lru_cache(maxsize=1)
def get_product() -> Product:
    return load_product()
