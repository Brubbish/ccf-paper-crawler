"""Crossref REST API：期刊按 ISSN 枚举；会议卷通过两阶段发现确切 container-title 后精确过滤。

注意：DBLP 已被 Anubis 反爬拦截，元数据一律走 Crossref。
"""
from __future__ import annotations

import logging
import re
import time

from ..config import Config
from ..models import PaperRecord
from ..registry import VenueSpec

log = logging.getLogger("crossref")
BASE = "https://api.crossref.org"
# 只取需要的字段：全量记录带 references，单页几十 MB 会拖慢解析；journal-issue 不是合法 select 字段，用标量 issue
SELECT_WORKS = "DOI,URL,title,author,issued,type,container-title,link,volume,issue"


class CollectorError(RuntimeError):
    pass


class Crossref:
    def __init__(self, http, mailto: str = ""):
        self.http = http
        # Crossref polite pool：无有效邮箱时给一个中性标识，避免被限流
        self.mailto = mailto if (mailto and "example.com" not in mailto) \
            else "ccf-pdf-crawler@users.noreply.github.com"

    def _get(self, path: str, params: dict) -> dict:
        params = dict(params)
        if self.mailto:
            params.setdefault("mailto", self.mailto)
        last = None
        for attempt in range(4):
            r = self.http.get(f"{BASE}{path}", params=params)
            if r.status_code == 200:
                return r.json()["message"]
            if r.status_code in (404, 400):
                raise CollectorError(f"Crossref HTTP {r.status_code}: {path} {params.get('filter', '')}")
            if r.status_code in (429, 500, 502, 503):
                ra = r.headers.get("retry-after", "")
                wait = float(ra) if ra.replace(".", "").isdigit() else 5.0 * (attempt + 1)
                time.sleep(min(wait, 30.0))
                last = f"HTTP {r.status_code}"
                continue
            raise CollectorError(f"Crossref HTTP {r.status_code}: {path}")
        raise CollectorError(f"Crossref 重试后仍失败: {last}")

    # ---------- 期刊 ----------
    def resolve_issn(self, spec: VenueSpec) -> str:
        if spec.issn:
            return spec.issn
        items = self._get("/journals", {"query": spec.journal_query, "rows": 5}).get("items", [])
        q = (spec.journal_query or "").lower()
        for it in items:
            t = (it.get("title") or "").lower()
            if q in t or t in q:
                return (it.get("ISSN") or [""])[0]
        if items:
            return (items[0].get("ISSN") or [""])[0]
        raise CollectorError(f"无法解析 ISSN: {spec.journal_query}")

    def journal_works(self, issn: str, year_from: int, year_to: int):
        flt = f"from-pub-date:{year_from}-01-01,until-pub-date:{year_to}-12-31"
        cursor = "*"
        for _ in range(60):
            msg = self._get(f"/journals/{issn}/works",
                            {"filter": flt, "rows": 500, "cursor": cursor, "select": SELECT_WORKS})
            items = msg.get("items", [])
            if not items:
                return
            yield from items
            nc = msg.get("next-cursor")
            if not nc:
                return
            cursor = nc

    # ---------- 会议卷 ----------
    def canonical_container(self, queries: list, year: int) -> str:
        """检索 type:proceedings 的论文集记录本身（而非文章），标题权威且结果集小。

        评分：排除 workshop 卷 → 排除冒号分册（如 "...: Software Engineering in Practice"）
        → 标题含年份 → 标题最短。候选必须原样包含至少一个检索词。
        """
        counts: dict[str, int] = {}
        flt = f"type:proceedings,from-pub-date:{year - 1}-01-01,until-pub-date:{year + 1}-12-31"
        for q in queries:
            try:
                msg = self._get("/works", {"query.bibliographic": q, "filter": flt, "rows": 25,
                                           "select": "title"})
            except CollectorError:
                continue
            for it in msg.get("items", []):
                t = (it.get("title") or [""])[0]
                if t and any(q.lower() in t.lower() for q in queries):
                    counts[t] = counts.get(t, 0) + 1
        if not counts:
            raise CollectorError(f"Crossref 上找不到 {year} 年的会议卷: {queries}（论文集可能尚未出版/收录）")

        def score(t: str):
            tl = t.lower()
            return ("workshop" not in tl, ":" not in t, str(year) in t, -len(t))

        return max(counts, key=score)

    def work_title(self, doi: str) -> str | None:
        """取 proceedings 记录自身的标题（= 该卷文章的确切 container-title）。"""
        try:
            msg = self._get(f"/works/{doi}", {})
        except CollectorError:
            return None
        t = (msg.get("title") or [""])[0]
        return t or None

    def proceedings_works(self, container: str, year: int) -> list:
        # container-title 精确 filter 不支持空格/逗号等特殊字符时会 400，退化为模糊查询 + 客户端精确匹配。
        # 模糊查询按相关度排序，主卷论文排最前，限 2 页避免在全库模糊命中里翻页。
        try:
            flt = f"container-title:{container.split(',')[0]}"
            got = list(self._paged({"filter": flt, "rows": 1000, "select": SELECT_WORKS}))
            exact = [w for w in got if (w.get("container-title") or [""])[0].lower() == container.lower()]
            if exact:
                return exact
        except CollectorError:
            pass
        log.info("精确 container-title 过滤无结果，退化为模糊查询: %s", container)
        flt = f"type:proceedings-article,from-pub-date:{year - 1}-01-01,until-pub-date:{year + 1}-12-31"
        got = list(self._paged({"query.container-title": container, "filter": flt, "rows": 1000,
                                "select": SELECT_WORKS}, max_pages=2))
        return [w for w in got if (w.get("container-title") or [""])[0].lower() == container.lower()]

    def _paged(self, params: dict, max_pages: int = 50):
        cursor = "*"
        for _ in range(max_pages):
            msg = self._get("/works", {**params, "cursor": cursor})
            items = msg.get("items", [])
            if not items:
                return
            yield from items
            nc = msg.get("next-cursor")
            if not nc:
                return
            cursor = nc


def work_to_record(w: dict, spec: VenueSpec, year: int) -> PaperRecord | None:
    titles = w.get("title") or []
    title = re.sub(r"\s+", " ", titles[0]).strip() if titles else ""
    if not title:
        return None
    authors = []
    for a in w.get("author") or []:
        nm = a.get("family") or a.get("name")
        if nm:
            authors.append(nm.strip())
    alt = ""
    for l in w.get("link") or []:
        if "application/pdf" in (l.get("content-type") or "") and l.get("URL"):
            alt = l["URL"]
            break
    cont = w.get("container-title") or [""]
    doi = (w.get("DOI") or "").strip()
    rid = doi or f"{spec.key}/{year}/{slug70(title)}"
    issue = w.get("issue") or (w.get("journal-issue") or {}).get("issue") or ""
    return PaperRecord(
        id=rid, area=spec.area, venue=spec.key, year=year, title=title, authors=authors,
        doi=doi, landing_url=w.get("URL") or "", alt_pdf_url=alt,
        container=cont[0] if cont else "", issue=issue,
    )


def slug70(title: str) -> str:
    from ..models import slugify
    return slugify(title, 70)


def issued_year(w: dict):
    parts = (w.get("issued") or {}).get("date-parts") or [[None]]
    return parts[0][0]


def collect_journal(cr: Crossref, spec: VenueSpec, year: int, limit=None) -> list:
    issn = cr.resolve_issn(spec)
    out = []
    for w in cr.journal_works(issn, year, year):
        if (w.get("type") or "") not in ("journal-article", "proceedings-article"):
            continue
        if issued_year(w) != year:
            continue
        r = work_to_record(w, spec, year)
        if r:
            out.append(r)
            if limit and len(out) >= limit:
                break
    return out


def collect_journal_issue(cr: Crossref, spec: VenueSpec, year: int, limit=None) -> list:
    """PACM* 期刊型会议：volume = year - volume_base_year + 1，issue 前缀 = issue_tag。"""
    issn = cr.resolve_issn(spec)
    vol = str(year - spec.volume_base_year + 1)
    tag = (spec.issue_tag or "").upper()
    out, seen = [], set()
    for w in cr.journal_works(issn, year - 1, year + 1):
        issue = (w.get("issue") or (w.get("journal-issue") or {}).get("issue") or "").upper()
        if not issue.startswith(tag):
            continue
        if (w.get("volume") or "") != vol and issued_year(w) != year:
            continue
        r = work_to_record(w, spec, year)
        if r and r.id not in seen:
            seen.add(r.id)
            out.append(r)
            if limit and len(out) >= limit:
                break
    if len(out) < 10:
        log.warning("[%s %s] 仅匹配到 %d 篇（volume=%s, issue 前缀=%s），请检查卷号映射",
                    spec.key, year, len(out), vol, tag)
    return out


def collect_proceedings(cr: Crossref, spec: VenueSpec, year: int, cfg: Config, limit=None) -> list:
    override = (cfg.container_overrides or {}).get(f"{spec.key}-{year}")
    container = override or cr.canonical_container(spec.proc_queries or [spec.key], year)
    log.info("[%s %s] 会议卷 container-title: %s", spec.key, year, container)
    out = []
    for w in cr.proceedings_works(container, year):
        if (w.get("type") or "") != "proceedings-article":
            continue
        r = work_to_record(w, spec, year)
        if r:
            out.append(r)
            if limit and len(out) >= limit:
                break
    return out
