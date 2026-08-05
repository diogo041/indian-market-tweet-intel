"""Selenium fallback backend: DOM scraping against x.com search.

This exists as insurance rather than as the default. `twscrape` reads the
private GraphQL endpoints and returns typed JSON with engagement counts,
entity spans, and exact timestamps already parsed; reconstructing those
from rendered markup is strictly more work and strictly more fragile at the
field level. But the two backends fail for unrelated reasons -- GraphQL
operation IDs rotate, the DOM is restructured on a different schedule -- so
having both means a single upstream change cannot stop collection entirely.

Anti-detection measures, in order of importance:

  1. `undetected_chromedriver` patches the automation flags that X
     fingerprints, notably `navigator.webdriver`.
  2. A persistent profile directory preserves cookies and local storage
     across runs, so the session looks continuous rather than freshly
     minted on every launch.
  3. Scrolling uses randomised distances and pauses. Fixed-interval,
     fixed-distance scrolling is a strong automation signal, since humans
     do not scroll in exact increments.
  4. Headless mode is off by default. X fingerprints headless Chrome
     through WebGL and font enumeration; a visible window costs resources
     but is considerably less detectable.

Known limitations, stated rather than hidden: view counts are not exposed
in the DOM at all, and engagement counts are rendered abbreviated ("1.2K"),
so they are parsed approximately. Anything requiring exact metrics should
use the GraphQL backend.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from mkt_intel.scraper.base import ScraperBackend

log = logging.getLogger(__name__)

_COUNT_RE = re.compile(r"^([\d.,]+)\s*([KMB]?)$", re.IGNORECASE)
_MULTIPLIER = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def parse_count(text: str) -> int:
    """Parse an abbreviated engagement count: '1.2K' -> 1200.

    X renders counts abbreviated in the timeline, so DOM-derived engagement
    is approximate above 1,000. Exact values require the GraphQL surface.
    """
    if not text:
        return 0
    m = _COUNT_RE.match(text.strip())
    if not m:
        return 0
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0
    return int(value * _MULTIPLIER[m.group(2).upper()])


class SeleniumBackend(ScraperBackend):
    """Collect tweets by driving a real browser against x.com/search."""

    SEARCH_URL = "https://x.com/search?q={query}&f=live"

    def __init__(
        self,
        auth_token: str,
        ct0: str,
        profile_dir: Path = Path("chrome_profile"),
        headless: bool = False,
        page_load_timeout: int = 30,
    ) -> None:
        self._auth_token = auth_token
        self._ct0 = ct0
        self._profile_dir = profile_dir
        self._headless = headless
        self._timeout = page_load_timeout
        self._driver = None

    def _ensure_driver(self):
        """Lazily construct the browser so import never launches Chrome."""
        if self._driver is not None:
            return self._driver

        import undetected_chromedriver as uc

        opts = uc.ChromeOptions()
        # A persistent profile keeps the session looking continuous across
        # runs rather than newly created on every launch.
        opts.add_argument(f"--user-data-dir={self._profile_dir.absolute()}")
        opts.add_argument("--window-size=1400,900")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        if self._headless:
            opts.add_argument("--headless=new")

        driver = uc.Chrome(options=opts)
        driver.set_page_load_timeout(self._timeout)

        # Cookies can only be set for the current domain, so load a cheap
        # page on x.com first.
        driver.get("https://x.com/robots.txt")
        for name, value in (("auth_token", self._auth_token), ("ct0", self._ct0)):
            driver.add_cookie({"name": name, "value": value, "domain": ".x.com"})

        self._driver = driver
        return driver

    @staticmethod
    def _parse_article(article) -> dict | None:
        """Extract one tweet from an <article> element.

        Returns None rather than raising on structural surprises: X ships
        promoted posts, "who to follow" cards, and error placeholders into
        the same article slots, and a partially-rendered element should
        cost one tweet rather than the whole run.
        """
        from selenium.webdriver.common.by import By

        try:
            time_el = article.find_element(By.CSS_SELECTOR, "time")
            created = time_el.get_attribute("datetime")
            permalink = time_el.find_element(By.XPATH, "..").get_attribute("href")
            tweet_id = int(permalink.rstrip("/").split("/")[-1])
            username = permalink.split("/")[3]

            text_el = article.find_elements(
                By.CSS_SELECTOR, '[data-testid="tweetText"]'
            )
            content = text_el[0].text if text_el else ""
            if not content:
                return None

            counts: dict[str, int] = {}
            for key, testid in (
                ("replyCount", "reply"),
                ("retweetCount", "retweet"),
                ("likeCount", "like"),
            ):
                els = article.find_elements(
                    By.CSS_SELECTOR, f'[data-testid="{testid}"]'
                )
                counts[key] = parse_count(els[0].text) if els else 0

            return {
                "id": tweet_id,
                "date": datetime.fromisoformat(
                    created.replace("Z", "+00:00")
                ).astimezone(timezone.utc).isoformat(),
                "rawContent": content,
                "user": {"username": username, "id": 0, "followersCount": 0},
                "replyCount": counts["replyCount"],
                "retweetCount": counts["retweetCount"],
                "likeCount": counts["likeCount"],
                "quoteCount": 0,
                "viewCount": 0,  # not exposed in the DOM
                "hashtags": re.findall(r"#(\w+)", content),
                "cashtags": re.findall(r"\$([A-Za-z]{1,6})", content),
                "mentionedUsers": [
                    {"username": u} for u in re.findall(r"@(\w+)", content)
                ],
                "lang": "und",
                "_backend": "selenium",
            }
        except Exception:
            return None

    async def search(self, query: str, limit: int = 300) -> AsyncIterator[dict]:
        """Scroll the Latest tab, yielding tweets until `limit` or stall."""
        from selenium.webdriver.common.by import By
        from urllib.parse import quote

        driver = await asyncio.to_thread(self._ensure_driver)
        await asyncio.to_thread(driver.get, self.SEARCH_URL.format(query=quote(query)))
        await asyncio.sleep(random.uniform(2.5, 4.0))

        seen: set[int] = set()
        stalls = 0

        while len(seen) < limit and stalls < 3:
            articles = await asyncio.to_thread(
                driver.find_elements, By.CSS_SELECTOR, "article"
            )
            before = len(seen)

            for article in articles:
                rec = self._parse_article(article)
                if rec and rec["id"] not in seen:
                    seen.add(rec["id"])
                    yield rec
                    if len(seen) >= limit:
                        return

            # Randomised scroll distance and pause: fixed increments are a
            # strong automation signal.
            await asyncio.to_thread(
                driver.execute_script,
                f"window.scrollBy(0, {random.randint(700, 1400)});",
            )
            await asyncio.sleep(random.uniform(1.2, 2.8))

            stalls = stalls + 1 if len(seen) == before else 0

        log.info("selenium: %d tweets for %s", len(seen), query[:50])

    async def close(self) -> None:
        if self._driver is not None:
            await asyncio.to_thread(self._driver.quit)
            self._driver = None
