from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree import ElementTree as ET


def _text(el: ET.Element, tag: str, default: str = "") -> str:
    child = el.find(tag)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def _int(el: ET.Element, tag: str, default: int = 0) -> int:
    try:
        return int(_text(el, tag, str(default)) or default)
    except ValueError:
        return default


@dataclass
class WsFile:
    ident: str
    name: str
    size: int
    type: str = ""
    img: str = ""
    positive_votes: int = 0
    negative_votes: int = 0
    password: bool = False
    queued: bool = False

    @classmethod
    def from_xml(cls, el: ET.Element) -> WsFile:
        return cls(
            ident=_text(el, "ident"),
            name=_text(el, "name"),
            size=_int(el, "size"),
            type=_text(el, "type"),
            img=_text(el, "img"),
            positive_votes=_int(el, "positive_votes"),
            negative_votes=_int(el, "negative_votes"),
            password=_text(el, "password", "0") not in ("", "0"),
            queued=_text(el, "queued", "0") not in ("", "0"),
        )

    def to_dict(self) -> dict:
        return {
            "ident": self.ident,
            "name": self.name,
            "size": self.size,
            "type": self.type,
            "img": self.img,
            "positive_votes": self.positive_votes,
            "negative_votes": self.negative_votes,
            "password": self.password,
            "queued": self.queued,
        }

    @classmethod
    def from_dict(cls, data: dict) -> WsFile:
        return cls(**data)


@dataclass
class WsSearchPage:
    total: int
    files: list[WsFile] = field(default_factory=list)
