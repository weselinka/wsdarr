"""NZB "envelopes" carrying a Webshare file ident, and signed release ids for ``t=get``.

The NZB is never sent to a Usenet server: it only transports the Webshare ident from the
indexer side of wsdarr, through Sonarr/Radarr (which validate it as a normal NZB), to the
SABnzbd side of wsdarr.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from xml.etree import ElementTree as ET

NZB_NS = "http://www.newzbin.com/DTD/2003/nzb"
DOCTYPE = '<!DOCTYPE nzb PUBLIC "-//newzBin//DTD NZB 1.1//EN" "http://www.newzbin.com/DTD/nzb/nzb-1.1.dtd">'


@dataclass
class NzbInfo:
    ident: str
    title: str
    ws_name: str = ""
    size: int = 0


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encode_release_id(secret: str, info: NzbInfo) -> str:
    payload = _b64e(
        json.dumps(
            {"i": info.ident, "t": info.title, "n": info.ws_name, "s": info.size},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    )
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:20]
    return f"{payload}.{sig}"


def decode_release_id(secret: str, token: str) -> NzbInfo:
    try:
        payload, sig = token.rsplit(".", 1)
    except ValueError as exc:
        raise ValueError("malformed release id") from exc
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:20]
    if not hmac.compare_digest(expected, sig):
        raise ValueError("invalid release id signature")
    data = json.loads(_b64d(payload))
    return NzbInfo(ident=data["i"], title=data["t"], ws_name=data.get("n", ""), size=int(data.get("s", 0)))


def build_nzb(info: NzbInfo) -> bytes:
    ET.register_namespace("", NZB_NS)
    root = ET.Element(f"{{{NZB_NS}}}nzb")
    head = ET.SubElement(root, f"{{{NZB_NS}}}head")
    for key, value in (
        ("title", info.title),
        ("x-wsdarr-ident", info.ident),
        ("x-wsdarr-name", info.ws_name),
        ("x-wsdarr-size", str(info.size)),
    ):
        meta = ET.SubElement(head, f"{{{NZB_NS}}}meta", {"type": key})
        meta.text = value
    ext = info.ws_name.rsplit(".", 1)[-1] if "." in info.ws_name else "mkv"
    file_el = ET.SubElement(
        root,
        f"{{{NZB_NS}}}file",
        {
            "poster": "wsdarr@webshare.cz",
            "date": str(int(time.time())),
            "subject": f'{info.title} [webshare:{info.ident}] "{info.title}.{ext}" yEnc (1/1)',
        },
    )
    groups = ET.SubElement(file_el, f"{{{NZB_NS}}}groups")
    ET.SubElement(groups, f"{{{NZB_NS}}}group").text = "alt.binaries.webshare"
    segments = ET.SubElement(file_el, f"{{{NZB_NS}}}segments")
    seg = ET.SubElement(segments, f"{{{NZB_NS}}}segment", {"bytes": str(info.size), "number": "1"})
    seg.text = f"{info.ident}@webshare.cz"
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{DOCTYPE}\n{body}\n'.encode()


def parse_nzb(content: bytes | str) -> NzbInfo:
    """Extract the Webshare ident from an NZB produced by :func:`build_nzb`."""
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    # ElementTree cannot handle the DOCTYPE declaration's external DTD; drop it.
    content = "\n".join(line for line in content.splitlines() if not line.lstrip().startswith("<!DOCTYPE"))
    try:
        root = ET.fromstring(content.encode())
    except ET.ParseError as exc:
        raise ValueError(f"invalid NZB: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1] != "nzb":
        raise ValueError("invalid NZB: root element is not <nzb>")
    meta: dict[str, str] = {}
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "meta" and el.get("type"):
            meta[el.get("type")] = (el.text or "").strip()
    ident = meta.get("x-wsdarr-ident")
    if not ident:
        raise ValueError("NZB does not come from wsdarr (no Webshare ident)")
    try:
        size = int(meta.get("x-wsdarr-size") or 0)
    except ValueError:
        size = 0
    return NzbInfo(ident=ident, title=meta.get("title", ""), ws_name=meta.get("x-wsdarr-name", ""), size=size)
