"""Newznab XML documents (caps, RSS search results, errors)."""

from __future__ import annotations

import hashlib
from email.utils import formatdate
from xml.etree import ElementTree as ET

from .. import __version__
from ..search import Release

NEWZNAB_NS = "http://www.newznab.com/DTD/2010/feeds/attributes/"
ATOM_NS = "http://www.w3.org/2005/Atom"

CATEGORIES = [
    (2000, "Movies", [(2030, "Movies/SD"), (2040, "Movies/HD"), (2045, "Movies/UHD")]),
    (5000, "TV", [(5030, "TV/SD"), (5040, "TV/HD"), (5045, "TV/UHD")]),
]
CATEGORY_NAMES = {cid: name for cid, name, subs in CATEGORIES} | {
    sid: sname for _, _, subs in CATEGORIES for sid, sname in subs
}

ERRORS = {
    100: "Incorrect user credentials",
    200: "Missing parameter",
    201: "Incorrect parameter",
    202: "No such function",
    300: "No such item",
    900: "Unknown error",
}


def _doc(root: ET.Element) -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="utf-8", xml_declaration=False
    )


def error_xml(code: int, description: str | None = None) -> bytes:
    return _doc(ET.Element("error", {"code": str(code), "description": description or ERRORS.get(code, "")}))


def caps_xml(public_url: str, page_size: int = 100) -> bytes:
    caps = ET.Element("caps")
    ET.SubElement(
        caps,
        "server",
        {
            "version": __version__,
            "title": "wsdarr",
            "strapline": "Webshare.cz for the *arr stack",
            "url": public_url,
        },
    )
    ET.SubElement(caps, "limits", {"max": str(page_size), "default": str(page_size)})
    ET.SubElement(caps, "registration", {"available": "no", "open": "no"})
    searching = ET.SubElement(caps, "searching")
    ET.SubElement(searching, "search", {"available": "yes", "supportedParams": "q"})
    ET.SubElement(
        searching, "tv-search", {"available": "yes", "supportedParams": "q,season,ep,tvdbid,imdbid,tmdbid"}
    )
    ET.SubElement(searching, "movie-search", {"available": "yes", "supportedParams": "q,imdbid,tmdbid"})
    ET.SubElement(searching, "audio-search", {"available": "no", "supportedParams": ""})
    ET.SubElement(searching, "book-search", {"available": "no", "supportedParams": ""})
    cats = ET.SubElement(caps, "categories")
    for cid, name, subs in CATEGORIES:
        cat = ET.SubElement(cats, "category", {"id": str(cid), "name": name})
        for sid, sname in subs:
            ET.SubElement(cat, "subcat", {"id": str(sid), "name": sname})
    return _doc(caps)


def release_guid(release: Release) -> str:
    digest = hashlib.sha1(release.title.encode()).hexdigest()[:10]
    return f"wsdarr-{release.ident}-{digest}"


def rss_xml(
    releases: list[Release],
    nzb_url,
    public_url: str,
    offset: int = 0,
    total: int | None = None,
) -> bytes:
    """``nzb_url`` is a callable ``Release -> str`` producing the ``t=get`` link."""
    ET.register_namespace("newznab", NEWZNAB_NS)
    ET.register_namespace("atom", ATOM_NS)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "wsdarr"
    ET.SubElement(channel, "description").text = "Webshare.cz search results"
    ET.SubElement(channel, "link").text = public_url
    ET.SubElement(
        channel,
        f"{{{NEWZNAB_NS}}}response",
        {"offset": str(offset), "total": str(total if total is not None else len(releases))},
    )

    for release in releases:
        url = nzb_url(release)
        pub = formatdate(release.pub_date, usegmt=True)
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = release.title
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = release_guid(release)
        ET.SubElement(item, "link").text = url
        ET.SubElement(item, "comments").text = f"https://webshare.cz/#/file/{release.ident}"
        ET.SubElement(item, "pubDate").text = pub
        ET.SubElement(item, "category").text = CATEGORY_NAMES.get(release.categories[-1], "")
        ET.SubElement(item, "description").text = release.ws_name
        ET.SubElement(
            item, "enclosure", {"url": url, "length": str(release.size), "type": "application/x-nzb"}
        )

        def attr(name: str, value, parent=item) -> None:
            ET.SubElement(parent, f"{{{NEWZNAB_NS}}}attr", {"name": name, "value": str(value)})

        for cat in release.categories:
            attr("category", cat)
        attr("size", release.size)
        attr("files", 1)
        attr("grabs", max(release.votes, 0))
        attr("usenetdate", pub)
        for language in release.languages:
            attr("language", language)
        if release.tvdb_id:
            attr("tvdbid", release.tvdb_id)
        if release.tmdb_id:
            attr("tmdbid", release.tmdb_id)
        if release.imdb_id:
            attr("imdb", release.imdb_id.removeprefix("tt"))
        if release.kind == "tv" and release.season is not None:
            attr("season", f"S{release.season:02d}")
            if release.episodes:
                attr("episode", f"E{release.episodes[0]:02d}")
    return _doc(rss)
