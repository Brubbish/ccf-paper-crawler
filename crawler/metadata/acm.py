"""ACM 会议卷采集：ACM DL proceedings 页（SSR，内嵌全部子 DOI）→ Crossref 按卷名精确枚举文章。

Crossref 的相关度检索无法可靠枚举会议卷（会被同名分册/其他会议淹没），所以：
1. 用内置的 proceedings DOI（registry.proc_doi / config.container_overrides）打开 ACM DL 目录页，
   正则提取本卷子 DOI（10.1145/<proc>.<n>），确认卷存在并拿到规模；
2. 取 proceedings 记录自身的 title 作为确切 container-title，用它精确枚举文章元数据。
"""
from __future__ import annotations

import logging
import re

from ..config import Config
from ..httpc import Http
from ..models import PaperRecord
from ..registry import VenueSpec
from .crossref import CollectorError, Crossref, work_to_record

log = logging.getLogger("acm")


def child_dois(http: Http, proc_doi: str) -> list:
    r = http.get(f"https://dl.acm.org/doi/proceedings/{proc_doi}")
    if r.status_code == 403:
        raise CollectorError("ACM DL 返回 403（反爬），建议安装 curl_cffi 或在 config.cookies 里配置 dl.acm.org 登录 cookie")
    if r.status_code != 200:
        raise CollectorError(f"ACM DL HTTP {r.status_code}: {proc_doi}")
    suffix = re.escape(proc_doi.split("/", 1)[1])
    dois = sorted(set(re.findall(rf"10\.1145/{suffix}\.\d+", r.content.decode("utf-8", "ignore"))))
    if not dois:
        raise CollectorError(f"ACM DL 目录页未解析出子 DOI: {proc_doi}（页面结构可能变化）")
    return dois


def collect(cr: Crossref, spec: VenueSpec, year: int, cfg: Config, http: Http, limit=None) -> list:
    doi = (cfg.container_overrides or {}).get(f"{spec.key}-{year}") \
        or (spec.proc_doi or {}).get(year)
    children = None
    # 注意：dl.acm.org 的目录页（/doi/proceedings/）受 Cloudflare 严格防护且非必需，
    # 默认不抓；PDF 端点（/doi/pdf/）防护宽松。acm_toc_check: true 时才做对账。
    if doi and getattr(cfg, "acm_toc_check", False) and doi.startswith("10.1145/"):
        try:
            children = child_dois(http, doi)
            log.info("[%s %s] ACM DL 目录解析出 %d 个子 DOI", spec.key, year, len(children))
        except CollectorError as e:
            log.warning("[%s %s] ACM DL 目录解析失败（仅用卷名枚举）: %s", spec.key, year, e)
    if doi:
        container = cr.work_title(doi)
        if not container:
            raise CollectorError(f"Crossref 上找不到 proceedings 记录 {doi}，"
                                 f"请在 config.container_overrides 里提供正确的 proceedings DOI")
        log.info("[%s %s] 会议卷 container-title: %s", spec.key, year, container)
    else:
        if spec.proc_doi:
            # 有 DOI 表的会议：表里没有的年份视为未出版，不做模糊检索兜底
            # （CCS 的检索词是 Asia CCS 标题的子串，模糊检索会抓错卷）
            raise CollectorError(
                f"{spec.key} {year} 无内置 proceedings DOI（大概率尚未出版），"
                f"已跳过；如已出版请在 registry.proc_doi 或 container_overrides 补充")
        container = cr.canonical_container(spec.proc_queries or [spec.key], year)
        log.info("[%s %s] 会议卷 container-title: %s（检索法，请核对；建议在 registry 补 proc_doi）",
                 spec.key, year, container)

    out = []
    for w in cr.proceedings_works(container, year):
        if (w.get("type") or "") != "proceedings-article":
            continue
        r = work_to_record(w, spec, year)
        if r:
            out.append(r)
            if limit and len(out) >= limit:
                break
    # DL 目录页只作对账（SSR 可能分段渲染不全，不能拿它做硬过滤）
    if children is not None and not limit and abs(len(out) - len(children)) > max(5, len(children) * 0.1):
        log.warning("[%s %s] 卷名枚举 %d 条 vs ACM DL 目录 %d 条，数量不一致，请核对",
                    spec.key, year, len(out), len(children))
    return out
