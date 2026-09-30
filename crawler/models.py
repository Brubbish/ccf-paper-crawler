from __future__ import annotations

import dataclasses
import re
import unicodedata


def slugify(text: str, maxlen: int = 110) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^0-9A-Za-z]+", "-", text).strip("-")
    return (text[:maxlen].rstrip("-") or "untitled")


@dataclasses.dataclass
class PaperRecord:
    id: str
    area: str
    venue: str
    year: int
    title: str
    authors: list = dataclasses.field(default_factory=list)
    doi: str = ""
    landing_url: str = ""
    pdf_url: str = ""       # 采集器拿到的官方直链（USENIX/NDSS）
    alt_pdf_url: str = ""   # Crossref link 里出版社登记的 PDF（Springer 等）
    container: str = ""
    issue: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PaperRecord":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in fields})

    @property
    def first_author(self) -> str:
        return self.authors[0] if self.authors else ""
