"""NDSS：官网 100% 免费。

ndssYYYY/accepted-papers → /ndss-paper/<slug>/ 页 → wp-content/uploads/<id>-paper.pdf
（PDF 文件名是内部 ID，必须从论文页解析，不能猜）
"""
from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from ..models import PaperRecord, slugify
from .crossref import CollectorError

log = logging.getLogger("ndss")


def _abs(url: str) -> str:
    return url if url.startswith("http") else "https://www.ndss-symposium.org" + url


def collect(http, spec, year: int, limit=None) -> list:
    idx = f"https://www.ndss-symposium.org/ndss{year}/accepted-papers/"
    r = http.get(idx)
    if r.status_code == 404:
        raise CollectorError(f"NDSS {year} accepted-papers 页面不存在: {idx}")
    if r.status_code != 200:
        raise CollectorError(f"NDSS HTTP {r.status_code}: {idx}")

    soup = BeautifulSoup(r.content, "lxml")
    seen, entries = set(), []
    for a in soup.select('a[href*="/ndss-paper/"]'):
        href = (a.get("href") or "").split("?")[0].rstrip("/") + "/"
        if not href or href in seen:
            continue
        # 排除分类/标签归档页
        tail = href.rstrip("/").rsplit("/", 1)[-1]
        if tail in ("category", "tag", "page", "ndss-paper"):
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
                for a2 in ps.select('a[href*="/wp-content/uploads/"]'):
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
            id=f"ndss/{year}/{slugify(title, 70)}",
            area=spec.area, venue=spec.key, year=year, title=title, authors=authors,
            pdf_url=pdf, landing_url=url, container=f"NDSS {year}",
        ))
    return records
