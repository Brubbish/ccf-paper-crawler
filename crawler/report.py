"""输出 manifest.csv（全部）与 missing.csv（未获取，含 DOI 链接供手动下载）。"""
from __future__ import annotations

import csv
import json
import logging

from .config import Config
from .download import State

log = logging.getLogger("report")

MANIFEST_COLS = ["area", "venue", "year", "title", "first_author", "doi", "doi_url",
                 "status", "source", "path", "error"]
MISSING_COLS = ["area", "venue", "year", "title", "doi", "doi_url", "error"]


def _load_all_papers(cfg: Config) -> list:
    papers_dir = cfg.output_dir / "_state" / "papers"
    out = []
    if not papers_dir.exists():
        return out
    for jf in sorted(papers_dir.rglob("*.jsonl")):
        for line in jf.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def write_reports(cfg: Config, state: State):
    rows, missing = [], []
    for rec in _load_all_papers(cfg):
        st = state.get(rec["id"]) or {}
        status = st.get("status", "pending")
        doi = rec.get("doi", "")
        row = {
            "area": rec["area"], "venue": rec["venue"], "year": rec["year"],
            "title": rec["title"], "first_author": rec["authors"][0] if rec.get("authors") else "",
            "doi": doi, "doi_url": f"https://doi.org/{doi}" if doi else "",
            "status": status, "source": st.get("source", ""), "path": st.get("path", ""),
            "error": st.get("error", ""),
        }
        rows.append(row)
        if status not in ("ok", "pending"):  # pending = 尚未尝试下载，不算未获取
            missing.append({k: row[k] for k in MISSING_COLS})

    manifest = cfg.output_dir / "manifest.csv"
    with manifest.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLS)
        w.writeheader()
        w.writerows(rows)
    miss = cfg.output_dir / "missing.csv"
    with miss.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MISSING_COLS)
        w.writeheader()
        w.writerows(missing)
    log.info("报告已写出: %s（%d 条）, %s（%d 条未获取）", manifest, len(rows), miss, len(missing))
