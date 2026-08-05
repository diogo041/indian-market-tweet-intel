"""Backend interface for tweet collection.

X's data surface is hostile and changes on its own schedule: GraphQL
operation IDs rotate every few weeks, and the DOM is restructured
independently of that. Binding the collector to either surface directly
would mean rewriting it whenever the chosen one moves.

This ABC lets the two available approaches sit behind one contract, so the
collector is written once and the backend is a configuration choice:

  * `TwscrapeBackend` reads the private GraphQL endpoints with session
    cookies. Fast, returns typed JSON, breaks when operation IDs rotate.
  * `SeleniumBackend` drives a real browser and parses rendered DOM. Slow
    and resource-hungry, but survives GraphQL changes entirely, since it
    consumes whatever the page renders.

Backends are expected to be resilient to their own failure modes and to
raise only on conditions the caller can act on -- authentication failure,
or a structural change that makes parsing impossible.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator


class ScraperBackend(ABC):
    """A source of tweets for a search query."""

    @abstractmethod
    async def search(self, query: str, limit: int = 300) -> AsyncIterator[dict]:
        """Yield raw tweet dicts matching `query`, up to `limit`.

        Implementations must yield dicts conforming to the field names
        consumed by `processing.clean.clean`: at minimum `id`, `date`,
        `rawContent`, and `user`. Missing optional fields are tolerated
        downstream and coerced to defaults.
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Release browser processes, connections, or other resources."""
        raise NotImplementedError

    @property
    def name(self) -> str:
        return type(self).__name__
