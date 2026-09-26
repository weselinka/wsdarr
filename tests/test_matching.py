import pytest

from wsdarr.matching.matcher import match, title_score
from wsdarr.matching.naming import build_release_title, newznab_languages
from wsdarr.matching.parser import detect_languages, parse_filename
from wsdarr.models import MediaContext


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "Pán prstenů - Návrat krále (2003) 1080p CZ dabing.mkv",
            {"year": 2003, "resolution": "1080p", "audio_languages": ["cs"], "episodes": []},
        ),
        (
            "Breaking.Bad.S01E02.720p.CZ.titulky.mkv",
            {
                "title": "Breaking Bad",
                "season": 1,
                "episodes": [2],
                "subtitle_languages": ["cs"],
                "audio_languages": [],
            },
        ),
        ("Simpsonovi 12x05 CZ.avi", {"title": "Simpsonovi", "season": 12, "episodes": [5]}),
        (
            "The.Matrix.1999.2160p.UHD.BluRay.x265.HDR.DTS-HD.MA.5.1-GROUP.mkv",
            {
                "year": 1999,
                "resolution": "2160p",
                "source": "BluRay",
                "video_codec": "x265",
                "hdr": "HDR",
                "audio_codec": "DTS-HD.MA",
                "audio_channels": "5.1",
            },
        ),
        ("Hra o truny S01E01 CZ tit 1080p WEB-DL.mkv", {"source": "WEB-DL", "subtitle_languages": ["cs"]}),
        (
            "Vetrelec.1979.Directors.Cut.BDRip.x264.CZ.SK.mkv",
            {"source": "BDRip", "edition": "Directors.Cut", "audio_languages": ["cs", "sk"]},
        ),
        ("Ordinace v růžové zahradě 2 - 5. série 12. díl.mp4", {"season": 5, "episodes": [12]}),
        ("Přátelé S01E01E02 CZ.mkv", {"season": 1, "episodes": [1, 2], "audio_languages": ["cs"]}),
        ("Film CZdab 2010.mkv", {"year": 2010, "audio_languages": ["cs"]}),
        ("Breaking.Bad.S01E02.sample.mkv", {"is_sample": True}),
    ],
)
def test_parse_filename(name, expected):
    parsed = parse_filename(name)
    for key, value in expected.items():
        assert getattr(parsed, key) == value, key


@pytest.mark.parametrize(
    ("name", "audio", "subs"),
    [
        ("Film (2010) CZ dabing.mkv", ["cs"], []),
        ("Film (2010) EN, CZ titulky.mkv", ["en"], ["cs"]),
        ("Film (2010) CZtit.mkv", [], ["cs"]),
        ("Film (2010) titulky.mkv", [], ["cs"]),
        ("Film (2010) dabing.mkv", ["cs"], []),
        ("Film (2010) SK dabing + CZ titulky.mkv", ["sk"], ["cs"]),
        ("Le.Film.en.francais.2010.mkv", [], []),
    ],
)
def test_detect_languages(name, audio, subs):
    assert detect_languages(name) == (audio, subs)


BB = MediaContext(
    kind="tv",
    titles=["Breaking Bad", "Perníkový táta"],
    canonical_title="Breaking Bad",
    season=1,
    episodes=[2],
    tvdb_id=81189,
    source="sonarr",
)
LOTR = MediaContext(
    kind="movie",
    titles=["The Lord of the Rings: The Return of the King", "Pán prstenů: Návrat krále"],
    canonical_title="The Lord of the Rings: The Return of the King",
    year=2003,
    tmdb_id=122,
    source="radarr",
)


@pytest.mark.parametrize(
    ("ctx", "name", "ok", "release"),
    [
        (
            BB,
            "Breaking.Bad.S01E02.720p.WEB-DL.CZ.titulky.mkv",
            True,
            "Breaking.Bad.S01E02.720p.WEB-DL.CZ.SUBS-WS",
        ),
        (
            BB,
            "Breaking Bad S01E02 CZ dabing 1080p BluRay x264.mkv",
            True,
            "Breaking.Bad.S01E02.1080p.BluRay.x264.CZ-WS",
        ),
        (BB, "Perníkový táta S01E02 (CZ) 720p.avi", True, "Breaking.Bad.S01E02.720p.CZ-WS"),
        (BB, "Breaking.Bad.S01E03.720p.CZ.mkv", False, None),
        (BB, "Breaking.Bad.S02E02.720p.CZ.mkv", False, None),
        (BB, "Breaking.Bad.S01E02.sample.mkv", False, None),
        (BB, "Breaking Bad S01E02.srt", False, None),
        (BB, "Better Call Saul S01E02 CZ.mkv", False, None),
        (
            LOTR,
            "Pán prstenů - Návrat krále (2003) 1080p CZ dabing.mkv",
            True,
            "The.Lord.of.the.Rings.The.Return.of.the.King.2003.1080p.CZ-WS",
        ),
        (
            LOTR,
            "Pán prstenů - Návrat krále / The Return of the King (2003) CZ.avi",
            True,
            "The.Lord.of.the.Rings.The.Return.of.the.King.2003.SDTV.CZ-WS",
        ),
        (LOTR, "Pan prstenu - Spolecenstvo prstenu (2001) CZ.avi", False, None),
        (LOTR, "Breaking.Bad.S01E02.720p.mkv", False, None),
    ],
)
def test_match_and_name(ctx, name, ok, release):
    parsed = parse_filename(name)
    result = match(parsed, ctx)
    assert result.ok is ok, result.reason
    if ok:
        assert build_release_title(parsed, ctx) == release


def test_numbers_must_agree():
    score, _ = title_score(["Ruzovy panter 2"], ["Růžový panter"])
    assert score == 0
    score, _ = title_score(["Ruzovy panter 2"], ["Růžový panter 2"])
    assert score == 100


def test_known_title_suffixes_are_ignored():
    score, _ = title_score(["The Office"], ["The Office (US)"])
    assert score == 100


def test_untrusted_context_keeps_parsed_title():
    parsed = parse_filename("Pelíšky (1999) 1080p CZ.mkv")
    ctx = MediaContext(kind="movie", titles=["Pelíšky"], source="query")
    assert build_release_title(parsed, ctx) == "Pelisky.1999.1080p.CZ-WS"
    assert newznab_languages(parsed) == ["Czech"]


@pytest.mark.parametrize(
    ("name", "hint", "expected"),
    [
        ("Simpsonovi 12x05 CZ.avi", True, "Simpsonovi.S12E05.SDTV.CZ-WS"),
        ("Simpsonovi 12x05 CZ.mkv", True, "Simpsonovi.S12E05.720p.HDTV.CZ-WS"),
        ("Simpsonovi 12x05 CZ.mkv", False, "Simpsonovi.S12E05.CZ-WS"),
        # Files that already carry a resolution or source get no hint.
        ("Simpsonovi 12x05 1080p CZ.mkv", True, "Simpsonovi.S12E05.1080p.CZ-WS"),
        ("Simpsonovi 12x05 DVDRip CZ.avi", True, "Simpsonovi.S12E05.DVDRip.CZ-WS"),
    ],
)
def test_unknown_quality_hint(name, hint, expected):
    assert build_release_title(parse_filename(name), None, quality_hint=hint) == expected
