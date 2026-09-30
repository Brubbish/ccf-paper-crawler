"""PDF 候选级联：官方直链 → 出版社直下（带 cookie）→ Unpaywall → OpenAlex → Semantic Scholar → arXiv。"""
from __future__ import annotations

import difflib
import logging
import re
import urllib.parse

from .config import Config

log = logging.getLogger("resolve")


class Resolver:
    def __init__(self, cfg: Config, http):
        self.cfg = cfg
        self.http = http
        self._email_ok = bool(cfg.unpaywall_email) and "example.com" not in cfg.unpaywall_email
        self._warned = False

    def sources(self, rec):
        """返回 [(来源名, url 或 返回 url 列表的 callable)]，按优先级排列。"""
        out = []
        if rec.pdf_url:
            out.append(("direct", rec.pdf_url))

        doi = (rec.doi or "").strip().lower()
        if doi:
            if doi.startswith("10.1145/"):
                out.append(("acm", f"https://dl.acm.org/doi/pdf/{doi}"))
            elif doi.startswith("10.1109/"):
                out.append(("ieee", lambda r=rec: self._ieee(r)))
            elif doi.startswith("10.1007/"):
                out.append(("springer", f"https://link.springer.com/content/pdf/{doi}.pdf"))
            if rec.alt_pdf_url:
                out.append(("crossref-pdf", rec.alt_pdf_url))

            fb = self.cfg.fallbacks or {}
            if fb.get("unpaywall", True):
                if self._email_ok:
                    out.append(("unpaywall", lambda d=doi: self._unpaywall(d)))
                elif not self._warned:
                    self._warned = True
                    log.warning("unpaywall_email 未配置（或仍是 example.com 占位符），跳过 Unpaywall 兜底")
            if fb.get("openalex", True):
                out.append(("openalex", lambda d=doi: self._openalex(d)))
            if fb.get("s2", True):
                out.append(("s2", lambda d=doi: self._s2(d)))
        if (self.cfg.fallbacks or {}).get("arxiv", True) and rec.title:
            out.append(("arxiv", lambda t=rec.title: self._arxiv(t)))
        return out

    # ---------- 出版社直下 ----------
    def _ieee(self, rec) -> list:
        """IEEE Xplore：arnumber ← landing_url 或 DOI 重定向；stamp.jsp 解析出真实 PDF，getPDF 兜底。需订阅 cookie。"""
        arn = None
        m = re.search(r"ieeexplore\.ieee\.org/document/(\d+)", rec.landing_url or "")
        if m:
            arn = m.group(1)
        if not arn and rec.doi:
            try:
                r = self.http.get(f"https://doi.org/{rec.doi}")
                m = re.search(r"document/(\d+)", str(r.url))
                arn = m.group(1) if m else None
            except Exception:
                pass
        if not arn:
            return []
        urls = []
        try:
            r = self.http.get(f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={arn}")
            if not r.is_pdf:
                m = re.search(rb'(?:https?://ieeexplore\.ieee\.org)?(/ielx?\d+/[^"\x27\s>]+\.pdf)', r.content)
                if m:
                    urls.append("https://ieeexplore.ieee.org" + m.group(1).decode("utf-8", "ignore"))
        except Exception:
            pass
        urls.append(f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={arn}&referrer=")
        return urls

    # ---------- OA 兜底 ----------
    def _unpaywall(self, doi: str) -> list:
        try:
            r = self.http.get(f"https://api.unpaywall.org/v2/{doi}",
                              params={"email": self.cfg.unpaywall_email})
            if r.status_code != 200:
                return []
            data = r.json()
        except Exception:
            return []
        urls = []
        locs = []
        if data.get("best_oa_location"):
            locs.append(data["best_oa_location"])
        locs.extend(data.get("oa_locations") or [])
        for l in locs:
            u = l.get("url_for_pdf")
            if u and u not in urls:
                urls.append(u)
        return urls

    def _openalex(self, doi: str) -> list:
        params = {"mailto": self.cfg.unpaywall_email} if self._email_ok else {}
        try:
            r = self.http.get(f"https://api.openalex.org/works/doi:{doi}", params=params)
            if r.status_code != 200:
                return []
            d = r.json()
        except Exception:
            return []
        urls = []
        oa = d.get("open_access") or {}
        if oa.get("oa_url"):
            urls.append(oa["oa_url"])
        locs = []
        if d.get("best_oa_location"):
            locs.append(d["best_oa_location"])
        locs.extend(d.get("locations") or [])
        for l in locs:
            u = l.get("pdf_url")
            if u and u not in urls:
                urls.append(u)
        return urls

    def _s2(self, doi: str) -> list:
        try:
            r = self.http.get(f"https://api.semanticscholar.org/graph/v1/papers/DOI:{doi}",
                              params={"fields": "openAccessPdf"})
            if r.status_code != 200:
                return []
            u = ((r.json().get("openAccessPdf")) or {}).get("url")
            return [u] if u else []
        except Exception:
            return []

    def _arxiv(self, title: str) -> list:
        t = title.replace('"', " ")
        q = urllib.parse.quote(f'ti:"{t}"')
        try:
            r = self.http.get(f"https://export.arxiv.org/api/query?search_query={q}&max_results=5")
            if r.status_code != 200:
                return []
        except Exception:
            return []
        text = r.content.decode("utf-8", "ignore")
        best_entry, best_ratio = None, 0.0
        for e in re.findall(r"<entry>(.*?)</entry>", text, re.S):
            m = re.search(r"<title>(.*?)</title>", e, re.S)
            if not m:
                continue
            t2 = re.sub(r"\s+", " ", m.group(1)).strip()
            ratio = difflib.SequenceMatcher(None, title.lower(), t2.lower()).ratio()
            if ratio > best_ratio:
                best_ratio, best_entry = ratio, e
        if not best_entry or best_ratio < 0.88:
            return []
        m = re.search(r"<id>(.*?)</id>", best_entry, re.S)
        if not m:
            return []
        aid = m.group(1).strip().replace("/abs/", "/pdf/")
        return [aid if aid.endswith(".pdf") else aid + ".pdf"]
