"""Build scene-like release titles that Sonarr/Radarr parse reliably.

Webshare file names are free-form (often Czech titles). When we know which item was searched
for (``MediaContext.trusted``), the release is renamed to the title Sonarr/Radarr use, keeping
only quality/language information extracted from the original name.

The one exception is a file name without any resolution/source: Sonarr/Radarr would parse its
quality as "Unknown" (rejected by the default profiles), so by default we add the quality they
themselves assign to such a file from its extension (``MediaFileExtensions``: mkv -> HDTV-720p,
avi/mp4 -> SDTV, ...).
"""

from __future__ import annotations

import re

from ..models import MediaContext
from .parser import ParsedFile, strip_diacritics

LANGUAGE_NAMES = {"cs": "Czech", "sk": "Slovak", "en": "English"}
# Tokens Sonarr/Radarr's LanguageParser map to a language (case sensitive, see LanguageParser.cs).
LANGUAGE_TOKENS = {"cs": "CZ", "sk": "SK"}
# Quality Sonarr/Radarr derive from the extension (MediaFileExtensions.cs), expressed as tokens.
EXTENSION_QUALITY = {
    "mkv": "720p.HDTV",
    "mk3d": "720p.HDTV",
    "m2ts": "720p.BluRay",
    "vob": "DVD",
    "iso": "DVD",
    "img": "DVD",
}


def dotify(text: str) -> str:
    text = strip_diacritics(text).replace("&", " and ")
    text = re.sub(r"['’`´]", "", text)
    text = re.sub(r"[^A-Za-z0-9]+", ".", text)
    return text.strip(".")


def quality_tokens(parsed: ParsedFile, quality_hint: bool = True) -> list[str]:
    toks: list[str] = []
    if parsed.edition:
        toks.append(parsed.edition)
    if parsed.resolution:
        toks.append(parsed.resolution)
    if parsed.source:
        toks.append(parsed.source)
    if quality_hint and not parsed.resolution and not parsed.source:
        toks.append(EXTENSION_QUALITY.get(parsed.extension, "SDTV"))
    if parsed.hdr:
        toks.append(parsed.hdr)
    if parsed.audio_codec:
        toks.append(parsed.audio_codec + (parsed.audio_channels or ""))
    if parsed.video_codec:
        toks.append(parsed.video_codec)
    return toks


def language_tokens(parsed: ParsedFile) -> list[str]:
    toks = [LANGUAGE_TOKENS[lang] for lang in parsed.audio_languages if lang in LANGUAGE_TOKENS]
    if "cs" in parsed.subtitle_languages and "cs" not in parsed.audio_languages:
        toks.append("CZ.SUBS")
    elif "sk" in parsed.subtitle_languages and "sk" not in parsed.audio_languages:
        toks.append("SK.SUBS")
    return toks


def newznab_languages(parsed: ParsedFile) -> list[str]:
    return [LANGUAGE_NAMES[lang] for lang in parsed.audio_languages if lang in LANGUAGE_NAMES]


def episode_token(parsed: ParsedFile, ctx: MediaContext | None = None) -> str:
    season = parsed.season if parsed.season is not None else (ctx.season if ctx else None)
    if season is None or not parsed.episodes:
        return ""
    return f"S{season:02d}" + "".join(f"E{e:02d}" for e in parsed.episodes)


def build_release_title(
    parsed: ParsedFile, ctx: MediaContext | None = None, group: str = "WS", quality_hint: bool = True
) -> str:
    kind = ctx.kind if ctx and ctx.kind != "unknown" else ("tv" if parsed.is_episode else "movie")
    if ctx and ctx.trusted and ctx.canonical_title:
        base = dotify(ctx.canonical_title)
        year = ctx.year or parsed.year
    else:
        base = dotify(parsed.title or (parsed.title_variants[0] if parsed.title_variants else parsed.raw))
        year = parsed.year

    parts = [base]
    if kind == "tv":
        ep = episode_token(parsed, ctx)
        if ep:
            parts.append(ep)
    elif year and not re.search(rf"(?:^|\.){year}$", base):
        parts.append(str(year))

    parts.extend(quality_tokens(parsed, quality_hint))
    parts.extend(language_tokens(parsed))
    title = ".".join(p for p in parts if p)
    return f"{title}-{group}" if group else title
