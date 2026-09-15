"""Address/name search ranking, shared by /check's carpark search and (via
lta_client.LiveAvailabilityStore.find_by_development_name) the fallback
live-lookup for carparks that have no clean ID to join on.

A query is treated as one phrase, not a bag of independently-scored words:
"changi business park" only matches carparks whose (normalized) address
contains that whole sequence of words together, not ones that merely
contain "changi" and "park" scattered in unrelated places. This holds for
single-word queries too — "jurong" still matches "JURONG POINT", including
as a substring of a longer word (e.g. "jur" matches "JURONG") — it just
means a query no longer gets partial credit for scattered word hits the
way a bag-of-words score used to (that used to let e.g. two carparks that
each shared just one of two query words rank above zero, which is exactly
the "2 separate words" behavior this was changed to avoid).
"""
from __future__ import annotations

import re
from typing import Protocol, TypeVar

_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]+")


def _normalize(text: str) -> str:
    """Uppercase and collapse punctuation/whitespace to single spaces.

    Lets "BLK 270/271", "BLK 270-271" and "BLK 270 271" all match each
    other — matching only cares about the sequence of alphanumeric words,
    not which punctuation an address or query happens to use between them.
    """
    return _NON_ALNUM_RE.sub(" ", text.upper()).strip()


def score_text_match(query_upper: str, text: str) -> float:
    """Relevance score of `text` against an already-uppercased query.

    Exposed standalone (not just inside rank_matches) so lta_client.py can
    reuse the identical scoring for matching free-text against LiveLot's
    `development` name, for carparks with no shared ID to join on directly
    (see carpark_rates_data.py's RateEntry, which has no car_park_no).
    """
    return 10.0 if _normalize(query_upper) in _normalize(text) else 0.0


class Nameable(Protocol):
    """What rank_matches() needs: CarparkInfo, UraCarpark, and RateEntry all
    satisfy this (the latter two via alias properties), so one ranking
    function works across all three static data sources."""

    address: str
    car_park_no: str


T = TypeVar("T", bound=Nameable)


def rank_matches(query: str, items: list[T], limit: int = 5) -> list[T]:
    """Return up to `limit` items ranked by relevance to `query`.

    Ties broken by shorter address (more likely to be the specific match
    the user meant, rather than a long address that happens to contain
    the query phrase incidentally).
    """
    q = query.strip()
    if not q:
        return []
    q_upper = q.upper()

    scored: list[tuple[float, int, T]] = []
    for item in items:
        score = score_text_match(q_upper, item.address)
        # Also let the item's own short code match (e.g. "/check ACB").
        # RateEntry's car_park_no is always "" so this is a no-op there.
        if item.car_park_no and q_upper == item.car_park_no.upper():
            score += 20.0
        if score > 0:
            scored.append((score, len(item.address), item))

    scored.sort(key=lambda t: (-t[0], t[1]))
    return [item for _, _, item in scored[:limit]]
