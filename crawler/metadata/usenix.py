"""USENIX Security：官网 100% 免费。

technical-sessions 页 → 每篇 /presentation/<slug> 页 → /system/files/usenixsecurityYY-<slug>.pdf
"""
from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from ..models import PaperRecord, slugify
from .crossref import CollectorError

log = logging.getLogger("usenix")


def _abs(url: str) -> str:
    return url if url.startswith("http") else "https://www.usenix.org" + url


def collect(http, spec, year: int, limit=None) -> list:
    yy = f"{year % 100:02d}"
    idx = f"https://www.usenix.org/conference/usenixsecurity{yy}/technical-sessions"
    r = http.get(idx)
    if r.status_code == 404:
        raise CollectorError(f"USENIX Security {year} 页面不存在（尚未举办或已下线）: {idx}")
    if r.status_code != 200:
        raise CollectorError(f"USENIX HTTP {r.status_code}: {idx}")

    soup = BeautifulSoup(r.content, "lxml")
    seen, entries = set(), []
    for a in soup.select('a[href*="/presentation/"]'):
        href = a.get("href")
        if not href or href in seen:
            continue
        seen.add(href)
        title = a.get_text(" ", strip=True)
        if len(title) >= 8:
            entries.append((href, title))
        if limit and len(entries) >= limit:
            break

    records = []
    for href, listing_title in entries:
        url = _abs(href)
        title, pdf, authors = listing_title, "", []
        try:
            pr = http.get(url)
            if pr.status_code == 200:
                ps = BeautifulSoup(pr.content, "lxml")
                for a2 in ps.select('a[href*="/system/files/"]'):
                    h = a2.get("href") or ""
                    if h.endswith(".pdf") and "slides" not in h and "appendix" not in h:
                        pdf = _abs(h)
                        break
                for m in ps.find_all("meta", attrs={"name": "citation_author"}):
                    if m.get("content"):
                        authors.append(m["content"].strip())
                h1 = ps.find("h1")
                if h1 and len(h1.get_text(strip=True)) >= 8:
                    title = h1.get_text(" ", strip=True)
        except Exception as e:
            log.debug("论文页抓取失败 %s: %s", url, e)
        records.append(PaperRecord(
            id=f"usenix/{year}/{slugify(title, 70)}",
            area=spec.area, venue=spec.key, year=year, title=title, authors=authors,
            pdf_url=pdf, landing_url=url, container=f"USENIX Security {year}",
        ))
    return records
