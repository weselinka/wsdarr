"""Parse Webshare file names (typically Czech/Slovak, often non-scene) into structured data.

guessit does the heavy lifting; on top of it we detect Czech/Slovak audio vs. subtitles
("CZ dabing", "CZ titulky", ...) and a few Czech season/episode notations.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from guessit import guessit

VIDEO_EXTENSIONS = {
    "mkv",
    "mp4",
    "avi",
    "m4v",
    "ts",
    "m2ts",
    "wmv",
    "mov",
    "mpg",
    "mpeg",
    "webm",
    "vob",
    "divx",
}

LANG_TOKENS = {
    "cz": "cs",
    "cze": "cs",
    "ces": "cs",
    "czech": "cs",
    "cesky": "cs",
    "cestina": "cs",
    "cesko": "cs",
    "sk": "sk",
    "svk": "sk",
    "slk": "sk",
    "slovak": "sk",
    "slovensky": "sk",
    "slovencina": "sk",
    "en": "en",
    "eng": "en",
    "english": "en",
    "anglicky": "en",
}
# Tokens where the language is glued to the marker ("CZdab", "CZtit", "SKdabing" ...).
GLUED_RE = re.compile(r"^(cz|sk|en|eng)(dab|dabing|dub|tit|titulky|sub|subs)$")
DUB_MARKERS = {"dabing", "dab", "dub", "dubbed", "dabovane", "dabovany", "dabovano", "zvuk", "audio"}
SUB_MARKERS = {"titulky", "tit", "titl", "sub", "subs", "subtitles", "subtitle", "titule", "forced"}
NOISE_WORDS = DUB_MARKERS | SUB_MARKERS

SOURCE_MAP = {
    "Ultra HD Blu-ray": "BluRay",
    "Blu-ray": "BluRay",
    "HD-DVD": "BluRay",
    "Web": "WEB-DL",
    "HDTV": "HDTV",
    "Ultra HDTV": "HDTV",
    "Digital TV": "HDTV",
    "TV": "SDTV",
    "Satellite": "HDTV",
    "DVD": "DVD",
    "Video on Demand": "WEB-DL",
    "Camera": "CAM",
    "HD Camera": "CAM",
    "Telesync": "TELESYNC",
    "HD Telesync": "TELESYNC",
    "Telecine": "TELECINE",
    "HD Telecine": "TELECINE",
    "Workprint": "WORKPRINT",
    "Screener": "SCREENER",
    "VHS": "VHSRip",
}
RIP_SOURCES = {"BluRay": "BDRip", "WEB-DL": "WEBRip", "DVD": "DVDRip", "HDTV": "HDTV", "SDTV": "SDTV"}
VIDEO_CODEC_MAP = {
    "H.264": "x264",
    "H.265": "x265",
    "Xvid": "XviD",
    "DivX": "DivX",
    "MPEG-2": "MPEG2",
    "VC-1": "VC-1",
    "AV1": "AV1",
    "VP9": "VP9",
}
AUDIO_CODEC_MAP = {
    "Dolby Digital": "DD",
    "Dolby Digital Plus": "DDP",
    "Dolby TrueHD": "TrueHD",
    "Dolby Atmos": "Atmos",
    "DTS": "DTS",
    "DTS-HD": "DTS-HD",
    "DTS:X": "DTS-X",
    "AAC": "AAC",
    "MP3": "MP3",
    "FLAC": "FLAC",
    "Opus": "Opus",
    "LPCM": "LPCM",
    "PCM": "PCM",
}
EDITION_MAP = {
    "Director's Cut": "Directors.Cut",
    "Extended": "Extended",
    "Remastered": "Remastered",
    "Unrated": "Unrated",
    "Uncut": "Uncut",
    "Theatrical": "Theatrical",
    "Special": "Special.Edition",
    "Collector": "Collectors.Edition",
    "Ultimate": "Ultimate.Edition",
    "IMAX": "IMAX",
}
RESOLUTION_NORMALIZE = {"4K": "2160p", "4k": "2160p", "2160i": "2160p", "1080i": "1080i"}

# Czech season/episode notations guessit does not know.
CZ_SE_PATTERNS = [
    # "1. série 2. díl", "1.serie - 02.dil", "1 rada 2 dil"
    re.compile(
        r"\b(\d{1,2})\s*\.?\s*(?:serie|seria|rada|sezona|sezon)\b\D{0,6}?(\d{1,3})\s*\.?\s*(?:dil|diel|epizoda|cast|ep)\b"
    ),
    # "série 1 díl 2", "serie 1, dil 02"
    re.compile(
        r"\b(?:serie|seria|rada|sezona|sezon)\s*(\d{1,2})\b\D{0,6}?(?:dil|diel|epizoda|cast|ep)\s*(\d{1,3})\b"
    ),
]
# Markers where a title ends in a raw file name.
TITLE_STOP_RE = re.compile(
    r"^(?:(?:19|20)\d{2}|s\d{1,2}|s\d{1,2}(?:e\d{1,3})+|(?:cz|sk)(?:dab|dabing|tit|titulky|sub|subs)|\d{1,2}x\d{1,3}|e\d{1,3}|\d{3,4}p|4k|uhd|hdr|bluray|bdrip|brrip|"
    r"webrip|web|webdl|hdtv|dvdrip|dvd|x264|x265|h264|h265|hevc|xvid|divx|cz|sk|cze|czech|eng|en|"
    r"dabing|titulky|tit|dab|serie|seria|rada|dil|diel|\d{1,2}\s*serie)$"
)


def strip_diacritics(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """Lower-case ASCII, '&' -> 'and', punctuation to spaces."""
    text = strip_diacritics(text).lower().replace("&", " and ")
    text = re.sub(r"['’`´]", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def tokens(text: str) -> list[str]:
    return normalize(text).split()


@dataclass
class ParsedFile:
    raw: str
    title: str = ""
    title_variants: list[str] = field(default_factory=list)
    year: int | None = None
    season: int | None = None
    episodes: list[int] = field(default_factory=list)
    resolution: str | None = None
    source: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    audio_channels: str | None = None
    hdr: str | None = None
    edition: str | None = None
    audio_languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)
    extension: str = ""
    is_sample: bool = False

    @property
    def is_episode(self) -> bool:
        return bool(self.episodes)

    @property
    def is_video(self) -> bool:
        return self.extension in VIDEO_EXTENSIONS


def detect_languages(name: str) -> tuple[list[str], list[str]]:
    """Return (audio_languages, subtitle_languages) as ISO 639-1 codes."""
    toks = tokens(name)
    audio: list[str] = []
    subs: list[str] = []

    def add(lst: list[str], lang: str) -> None:
        if lang not in lst:
            lst.append(lang)

    has_dub_marker = any(t in DUB_MARKERS for t in toks)
    has_sub_marker = any(t in SUB_MARKERS for t in toks)
    for i, tok in enumerate(toks):
        glued = GLUED_RE.match(tok)
        if glued:
            lang = LANG_TOKENS[glued.group(1)]
            add(subs if glued.group(2) in SUB_MARKERS else audio, lang)
            continue
        lang = LANG_TOKENS.get(tok)
        if not lang:
            continue
        # "en" is also a common word in titles; only trust it next to other language info.
        if tok == "en" and not any(LANG_TOKENS.get(t) for t in toks[max(0, i - 2) : i] + toks[i + 1 : i + 3]):
            continue
        nxt = toks[i + 1 : i + 3]
        prev = toks[i - 1] if i else ""
        if any(t in SUB_MARKERS for t in nxt[:1]) or prev in SUB_MARKERS:
            add(subs, lang)
        elif any(t in DUB_MARKERS for t in nxt[:1]) or prev in DUB_MARKERS:
            add(audio, lang)
        else:
            add(audio, lang)

    if not audio and has_dub_marker:
        audio.append("cs")
    if not subs and has_sub_marker and not any(LANG_TOKENS.get(t) for t in toks):
        subs.append("cs")
    return audio, subs


def _czech_season_episode(name: str) -> tuple[int, int] | None:
    text = normalize(name)
    for pattern in CZ_SE_PATTERNS:
        m = pattern.search(text)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def raw_title_variants(name: str) -> list[str]:
    """Title candidates cut from the raw name (before year/SxxEyy/quality markers)."""
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", name)
    stem = re.sub(r"[\[\(].*?[\]\)]", " ", stem)  # drop bracketed parts ("(2003)", "[CZ]")
    parts = re.split(r"\s+[-/|]\s+|\s*/\s*", stem)
    head_parts: list[str] = []
    for part in parts:
        toks = tokens(part)
        kept: list[str] = []
        stop = False
        for tok in toks:
            if TITLE_STOP_RE.match(tok):
                stop = True
                break
            kept.append(tok)
        if kept:
            head_parts.append(" ".join(kept))
        if stop:
            break
    variants: list[str] = []
    for p in head_parts:
        variants.append(p)
    for a, b in zip(head_parts, head_parts[1:], strict=False):
        variants.append(f"{a} {b}")
    if len(head_parts) > 2:
        variants.append(" ".join(head_parts))
    return [v for v in dict.fromkeys(variants) if v and not v.replace(" ", "").isdigit()]


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


@lru_cache(maxsize=4096)
def parse_filename(name: str) -> ParsedFile:
    audio, subs = detect_languages(name)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

    # Remove Czech dub/sub words so guessit does not mistake them for release groups/titles.
    cleaned = re.sub(r"(?i)(?<![A-Za-z])(?:cz|sk)?(?:dabing|dab|titulky|tit)(?![A-Za-z])", " ", name)
    info = dict(guessit(cleaned))

    parsed = ParsedFile(
        raw=name, extension=ext if ext in VIDEO_EXTENSIONS else str(info.get("container") or ext)
    )
    parsed.audio_languages = audio
    parsed.subtitle_languages = subs

    title = str(_first(info.get("title")) or "")
    alt = info.get("alternative_title")
    alts = [str(a) for a in (alt if isinstance(alt, list) else [alt])] if alt else []
    parsed.title = title
    variants = [title] if title else []
    if title and alts:
        variants.append(" ".join([title, *alts]))
        variants.extend(alts)
    if title and info.get("part"):
        variants.append(f"{title} part {info['part']}")
    variants.extend(raw_title_variants(name))
    parsed.title_variants = [
        v for v in dict.fromkeys(variants) if v and not normalize(v).replace(" ", "").isdigit()
    ]

    year = _first(info.get("year"))
    parsed.year = int(year) if year else None

    season = _first(info.get("season"))
    episode = info.get("episode")
    if episode is not None:
        eps = episode if isinstance(episode, list) else [episode]
        parsed.episodes = sorted({int(e) for e in eps if isinstance(e, int)})
        parsed.season = int(season) if isinstance(season, int) else None
    cz = _czech_season_episode(name)
    if cz and (not parsed.episodes or parsed.season is None):
        parsed.season, parsed.episodes = cz[0], [cz[1]]

    res = info.get("screen_size")
    if res:
        res = str(res)
        parsed.resolution = RESOLUTION_NORMALIZE.get(res, res)

    source = SOURCE_MAP.get(str(_first(info.get("source")) or ""))
    other = info.get("other") or []
    other = other if isinstance(other, list) else [other]
    if source and "Rip" in other:
        source = RIP_SOURCES.get(source, source)
    parsed.source = source

    vc = _first(info.get("video_codec"))
    parsed.video_codec = VIDEO_CODEC_MAP.get(str(vc)) if vc else None
    if "Dolby Vision" in other or "Dolby Vision" in str(info.get("video_profile", "")):
        parsed.hdr = "DV"
    elif any(str(o).startswith("HDR") for o in other):
        parsed.hdr = "HDR"

    ac = info.get("audio_codec")
    if ac:
        codecs = ac if isinstance(ac, list) else [ac]
        mapped = [AUDIO_CODEC_MAP[c] for c in map(str, codecs) if c in AUDIO_CODEC_MAP]
        if mapped:
            main = mapped[0]
            if main == "DTS-HD" and "Master Audio" in str(info.get("audio_profile", "")):
                main = "DTS-HD.MA"
            parsed.audio_codec = ".".join([main, *[m for m in mapped[1:] if m == "Atmos"]])
    ch = info.get("audio_channels")
    parsed.audio_channels = str(ch) if ch else None

    edition = _first(info.get("edition"))
    if edition:
        parsed.edition = EDITION_MAP.get(str(edition), re.sub(r"[^A-Za-z0-9]+", ".", str(edition)).strip("."))

    parsed.is_sample = "sample" in tokens(name) or "Sample" in other
    return parsed
