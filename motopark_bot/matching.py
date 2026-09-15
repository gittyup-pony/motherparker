"""Fuzzy-ish text ranking, shared by /check's carpark search and (via
lta_client.LiveAvailabilityStore.find_by_development_name) the fallback
live-lookup for carparks that have no clean ID to join on.

Not doing anything fancy — no external fuzzy-match dependency needed.
Scores by: exact substring hit (best), then token overlap, so "/check
jurong point" ranks "BLK ... JURONG POINT ..." above an unrelated carpark
that happens to share one word.
"""
from __future__ import annotations

from typing import Protocol, TypeVar


def _tokenize(text: str) -> set[str]:
    return {tok for tok in text.upper().replace("/", " ").split() if tok}


def score_text_match(query_upper: str, query_tokens: set[str], text: str) -> float:
    """Relevance score of `text` against an already-normalized query.

    Exposed standalone (not just inside rank_matches) so lta_client.py can
    reuse the identical scoring for matching free-text against LiveLot's
    `development` name, for carparks with no shared ID to join on directly
    (see carpark_rates_data.py's RateEntry, which has no car_park_no).
    """
    text_upper = text.upper()
    score = 10.0 if query_upper in text_upper else 0.0
    score += len(query_tokens & _tokenize(text))
    return score


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
    all their words incidentally).
    """
    q = query.strip()
    if not q:
        return []
    q_upper = q.upper()
    q_tokens = _tokenize(q)

    scored: list[tuple[float, int, T]] = []
    for item in items:
        score = score_text_match(q_upper, q_tokens, item.address)
        # Also let the item's own short code match (e.g. "/check ACB").
        # RateEntry's car_park_no is always "" so this is a no-op there.
        if item.car_park_no and q_upper == item.car_park_no.upper():
            score += 20.0
        if score > 0:
            scored.append((score, len(item.address), item))

    scored.sort(key=lambda t: (-t[0], t[1]))
    return [item for _, _, item in scored[:limit]]
