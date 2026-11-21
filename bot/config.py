import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "out"
REPORTS = ROOT / "reports"


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def env(name: str, required: bool = False, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if required and not value:
        raise SystemExit(f"Не задан секрет/переменная окружения {name}")
    return value


def log(msg: str) -> None:
    print(msg, flush=True)


def mask(secret: str | None) -> None:
    """Скрыть значение в логах GitHub Actions."""
    if secret and os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{secret}", flush=True)
