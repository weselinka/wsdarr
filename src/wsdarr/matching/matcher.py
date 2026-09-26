"""Decide whether a Webshare file is the movie/episode Sonarr/Radarr asked for."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from ..models import MediaContext
from .parser import ParsedFile, normalize

ARTICLES = {"the", "a", "an", "der", "die", "das", "le", "la", "les"}


@dataclass
class MatchResult:
    ok: bool
    score: int = 0
    reason: str = ""
    matched_title: str = ""


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def _strip_articles(text: str) -> str:
    toks = text.split()
    if toks and toks[0] in ARTICLES:
        toks = toks[1:]
    return " ".join(toks)


def known_title_variants(titles: list[str]) -> list[str]:
    """Normalized titles plus variants without "(US)"/"(2005)" suffixes and subtitles after ':'."""
    out: list[str] = []
    for title in titles:
        for variant in (
            title,
            re.sub(r"\s*\([^)]*\)\s*", " ", title),
            re.sub(r"\s+(?:19|20)\d{2}$", "", title.strip()),
        ):
            norm = normalize(variant)
            if norm and norm not in out:
                out.append(norm)
    return out


def title_score(candidates: list[str], known: list[str]) -> tuple[int, str]:
    best, best_title = 0, ""
    known_norm = known_title_variants(known)
    for cand in candidates:
        c = normalize(cand)
        if not c:
            continue
        for k in known_norm:
            # Numbers must agree: "Ruzovy panter 2" must not match "Ruzovy panter".
            if _numbers(c) != _numbers(k):
                continue
            score = max(fuzz.ratio(c, k), fuzz.ratio(_strip_articles(c), _strip_articles(k)))
            if score > best:
                best, best_title = int(score), k
    return best, best_title


def match(parsed: ParsedFile, ctx: MediaContext, threshold: int = 88) -> MatchResult:
    if parsed.is_sample:
        return MatchResult(False, reason="sample")
    if not parsed.is_video:
        return MatchResult(False, reason=f"not a video ({parsed.extension})")

    if ctx.kind == "movie":
        if parsed.is_episode:
            return MatchResult(False, reason="episode, movie wanted")
        if ctx.year and parsed.year and abs(ctx.year - parsed.year) > 1:
            return MatchResult(False, reason=f"year {parsed.year} != {ctx.year}")
    elif ctx.kind == "tv":
        if not parsed.is_episode:
            return MatchResult(False, reason="no episode number")
        if ctx.season is not None and parsed.season != ctx.season:
            return MatchResult(False, reason=f"season {parsed.season} != {ctx.season}")
        if ctx.episodes and not set(parsed.episodes) & set(ctx.episodes):
            return MatchResult(False, reason=f"episode {parsed.episodes} not in {ctx.episodes}")

    if not ctx.titles:
        return MatchResult(True, score=0, reason="no titles to compare")

    score, title = title_score(parsed.title_variants, ctx.titles)
    if score < threshold:
        return MatchResult(False, score=score, reason=f"title score {score} < {threshold}")
    return MatchResult(True, score=score, matched_title=title)
