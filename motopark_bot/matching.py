"""Fuzzy-ish ranking for "/check <free text>" against carpark addresses.

Not doing anything fancy — no external fuzzy-match dependency needed. This
scores by: exact substring hit (best), then token overlap, so "/check
jurong point" ranks "BLK ... JURONG POINT ..." above an unrelated carpark
that happens to share one word.
"""
from __future__ import annotations

from motopark_bot.static_data import CarparkInfo


def _tokenize(text: str) -> set[str]:
    return {tok for tok in text.upper().replace("/", " ").split() if tok}


def rank_matches(query: str, carparks: list[CarparkInfo], limit: int = 5) -> list[CarparkInfo]:
    """Return up to `limit` carparks ranked by relevance to `query`.

    Score = (substring match bonus) + (number of overlapping tokens).
    Ties broken by shorter address (more likely to be the specific match
    the user meant, rather than a long address that happens to contain
    all their words incidentally).
    """
    q = query.strip()
    if not q:
        return []
    q_upper = q.upper()
    q_tokens = _tokenize(q)

    scored: list[tuple[float, int, CarparkInfo]] = []
    for cp in carparks:
        addr_upper = cp.address.upper()
        score = 0.0
        if q_upper in addr_upper:
            score += 10.0
        overlap = len(q_tokens & _tokenize(cp.address))
        score += overlap
        # Also let the carpark's own short code match (e.g. "/check ACB").
        if q_upper == cp.car_park_no.upper():
            score += 20.0
        if score > 0:
            scored.append((score, len(cp.address), cp))

    scored.sort(key=lambda t: (-t[0], t[1]))
    return [cp for _, _, cp in scored[:limit]]
