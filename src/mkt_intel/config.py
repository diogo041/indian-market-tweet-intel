"""Credential and configuration loading."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Account:
    """A single authenticated X session."""

    username: str
    auth_token: str
    ct0: str

    @property
    def cookies(self) -> str:
        return f"auth_token={self.auth_token}; ct0={self.ct0}"


def load_accounts(max_accounts: int = 20) -> list[Account]:
    """Read X_ACCOUNT_N=label:auth_token:ct0 entries from the environment.

    Falls back to a single X_AUTH_TOKEN/X_CT0 pair for convenience.
    Raises if nothing is configured, since silent no-auth failures at X
    surface as empty result sets rather than errors.
    """
    accounts: list[Account] = []
    for i in range(1, max_accounts + 1):
        raw = os.getenv(f"X_ACCOUNT_{i}")
        if not raw:
            continue
        parts = [p.strip() for p in raw.split(":")]
        if len(parts) != 3:
            raise ValueError(
                f"X_ACCOUNT_{i} malformed: expected 'label:auth_token:ct0'"
            )
        accounts.append(Account(*parts))

    if not accounts and os.getenv("X_AUTH_TOKEN"):
        accounts.append(
            Account("burner1", os.environ["X_AUTH_TOKEN"], os.environ["X_CT0"])
        )
    if not accounts:
        raise RuntimeError("No X accounts configured; set X_ACCOUNT_1 in .env")
    return accounts