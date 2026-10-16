"""Loads settings from config/settings.yaml and secrets from .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


class ConfigError(Exception):
    """Settings or secrets are missing or unsafe."""


@dataclass(frozen=True)
class RiskLimits:
    max_position_pct: float
    max_daily_loss_pct: float
    kill_switch_drawdown_pct: float


@dataclass(frozen=True)
class VenueCosts:
    commission_bps: float
    spread_bps: float
    slippage_bps: float

    @property
    def total_bps(self) -> float:
        """Cost of one trade (one side), in basis points."""
        return self.commission_bps + self.spread_bps + self.slippage_bps


@dataclass(frozen=True)
class CloudSettings:
    r2_bucket: str
    d1_database_id: str
    timezone: str
    summary_times: tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    timeframe: str
    history_start: str
    stocks: tuple[str, ...]
    crypto: tuple[str, ...]
    benchmarks: dict[str, str]
    risk: RiskLimits
    costs: dict[str, VenueCosts]
    cloud: CloudSettings | None = None


@dataclass(frozen=True)
class AlpacaSecrets:
    api_key: str
    secret_key: str

    def __repr__(self) -> str:  # never print keys, even by accident
        return f"AlpacaSecrets(api_key='{self.api_key[:4]}…', secret_key='***')"


def load_settings(path: Path = DEFAULT_SETTINGS_PATH) -> Settings:
    raw = yaml.safe_load(Path(path).read_text())

    risk = RiskLimits(**raw["risk"])
    for name, value in vars(risk).items():
        if not 0 < value < 1:
            raise ConfigError(f"risk.{name} must be a fraction between 0 and 1, got {value}")

    return Settings(
        timeframe=raw["timeframe"],
        history_start=str(raw["history_start"]),
        stocks=tuple(raw["universe"]["stocks"]),
        crypto=tuple(raw["universe"]["crypto"]),
        benchmarks=dict(raw["benchmarks"]),
        risk=risk,
        costs={venue: VenueCosts(**c) for venue, c in raw["costs"].items()},
        cloud=CloudSettings(**{**raw["cloud"], "summary_times": tuple(raw["cloud"]["summary_times"])})
        if "cloud" in raw else None,
    )


def secret(name: str, env_file: Path | None = PROJECT_ROOT / ".env") -> str:
    """Read one secret from the environment (or .env). Raises a clear error if it's missing."""
    if env_file is not None:
        load_dotenv(env_file)
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is not set. Add it to .env (locally) or GitHub Secrets (for Actions).")
    return value


def load_alpaca_secrets(env_file: Path | None = PROJECT_ROOT / ".env") -> AlpacaSecrets:
    """Read Alpaca keys from the environment (or .env). Only paper keys are accepted."""
    if env_file is not None:
        load_dotenv(env_file)

    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()

    if not api_key or not secret_key:
        raise ConfigError(
            "Alpaca keys not found. Copy .env.example to .env and fill in your PAPER keys."
        )
    # Alpaca paper keys start with "PK"; live keys start with "AK". Refuse anything else,
    # so this project can never place a real-money order by accident.
    if not api_key.startswith("PK"):
        raise ConfigError(
            "ALPACA_API_KEY does not look like a paper-trading key (should start with 'PK'). "
            "This project only runs on paper money."
        )
    return AlpacaSecrets(api_key=api_key, secret_key=secret_key)
