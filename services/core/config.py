"""Fail-safe application configuration."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    """General services configuration; write credentials are intentionally absent."""

    app_env: str = "development"
    kalshi_subaccount: int = 0
    production_write_enabled: bool = False
    autonomous_trading_enabled: bool = False

    def __post_init__(self) -> None:
        """Reject unsafe general-service activation attempts."""
        if self.kalshi_subaccount != 0:
            raise ValueError("only primary subaccount 0 is permitted")
        if self.production_write_enabled or self.autonomous_trading_enabled:
            raise ValueError("production writes and autonomous trading require the isolated signer")
