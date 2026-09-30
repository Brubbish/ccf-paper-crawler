from __future__ import annotations

import json
import logging
import pathlib
import re

from ..config import Config
from ..httpc import Http
from ..models import PaperRecord
from ..registry import VenueSpec
from .crossref import CollectorError, Crossref
from . import crossref as cr_mod
from . import acm, ndss, usenix

log = logging.getLogger("metadata")

# 词边界匹配，避免把 "Demonstrating/Demonstration" 这类正常标题误当 demo track 过滤
_EXCLUDE_RES: list = []


def _compile(patterns: list) -> list:
    if len(_EXCLUDE_RES) != len(patterns) or any(
            getattr(p, "pattern", None) != str(q) for p, q in zip(_EXCLUDE_RES, patterns)):
        _EXCLUDE_RES.clear()
        for p in patterns:
            _EXCLUDE_RES.append(re.compile(r"\b" + re.escape(str(p).lower()) + r"\b"))
    return _EXCLUDE_RES


def _excluded(title: str, patterns: list) -> bool:
    t = title.lower()
    return any(rx.search(t) for rx in _compile(patterns))


def collect(spec: VenueSpec, year: int, cfg: Config, http: Http, cr: Crossref,
            refresh: bool = False, limit: int | None = None) -> list:
    """采集（带 jsonl 缓存）+ 过滤 poster/demo 等，返回 PaperRecord 列表。"""
    pdir = cfg.output_dir / "_state" / "papers" / spec.area
    pfile = pdir / f"{spec.key}-{year}.jsonl"
    if pfile.exists() and not refresh:
        recs = [PaperRecord.from_dict(json.loads(l))
                for l in pfile.read_text(encoding="utf-8").splitlines() if l.strip()]
        log.info("[%s %s] 使用缓存的 %d 条元数据（--refresh-metadata 重新采集）", spec.key, year, len(recs))
        return recs

    if spec.collector == "usenix":
        recs = usenix.collect(http, spec, year, limit)
    elif spec.collector == "ndss":
        recs = ndss.collect(http, spec, year, limit)
    elif spec.collector == "crossref_journal":
        recs = cr_mod.collect_journal(cr, spec, year, limit)
    elif spec.collector == "crossref_journal_issue" and spec.issue_from_year and year >= spec.issue_from_year:
        recs = cr_mod.collect_journal_issue(cr, spec, year, limit)
    elif (spec.proc_doi or {}).get(year):
        # 会议年代年份（FSE 2022/2023 等）优先用内置 proceedings DOI，避免模糊检索抓错卷
        recs = acm.collect(cr, spec, year, cfg, http, limit)
    elif spec.proc_doi:
        # 有 DOI 表的会议：表里没有的年份视为未出版（acm.collect 的防护会拒绝模糊检索兜底，
        # 防止 CCS 误抓 Asia CCS 这类同名子串卷）
        recs = acm.collect(cr, spec, year, cfg, http, limit)
    else:
        recs = cr_mod.collect_proceedings(cr, spec, year, cfg, limit)

    kept = [r for r in recs if not _excluded(r.title, cfg.exclude_patterns)]
    dropped = len(recs) - len(kept)
    if dropped:
        log.info("[%s %s] 按 exclude_patterns 过滤掉 %d 篇（poster/demo/keynote 等）", spec.key, year, dropped)

    pdir.mkdir(parents=True, exist_ok=True)
    with pfile.open("w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
    return kept
