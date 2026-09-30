"""下载器：断点续传（state.json）、PDF 校验、命名落盘（领域/会议/年份/第一作者_标题.pdf）。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time

from .config import Config
from .models import PaperRecord, slugify
from .resolve import Resolver

log = logging.getLogger("download")


class State:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                log.warning("state.json 损坏，重新开始（已下载文件仍会按 hash 去重跳过需手动）")

    def get(self, rid):
        with self.lock:
            return self.data.get(rid)

    def set(self, rid, entry):
        with self.lock:
            self.data[rid] = entry
            self._save()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)


def _target_path(rec: PaperRecord, cfg: Config, dup: bool = False):
    base = cfg.output_dir / rec.area / rec.venue / str(rec.year)
    last = slugify((rec.first_author or "").split()[-1], 40) if rec.first_author else ""
    slug = slugify(rec.title, 100)
    stem = f"{last}_{slug}" if last and last.lower() not in slug.lower() else slug
    if dup:
        stem += "-dup"
    return base / f"{stem}.pdf"


def _safe_urls(fn) -> list:
    try:
        return fn() or []
    except Exception as e:
        log.debug("候选源展开失败: %s", e)
        return []


def process_record(rec: PaperRecord, cfg: Config, http, resolver: Resolver, state: State,
                   seen_hashes: set, retry_missing: bool = False) -> str:
    """返回 ok / missing / skip-ok / skip-missing。"""
    st = state.get(rec.id)
    if st:
        if st.get("status") == "ok":
            p = cfg.output_dir / st.get("path", "")
            if p.exists():
                return "skip-ok"
        if st.get("status") == "missing" and not retry_missing:
            return "skip-missing"

    last_err = ""
    for name, src in resolver.sources(rec):
        urls = [src] if isinstance(src, str) else _safe_urls(src)
        if not urls and not isinstance(src, str):
            last_err = last_err or f"{name}: 无候选链接"
        for url in urls:
            try:
                r = http.get(url)
            except Exception as e:
                last_err = f"{name}: {e}"
                continue
            if r.status_code != 200:
                last_err = f"{name}: HTTP {r.status_code}"
                continue
            if not r.is_pdf:
                last_err = f"{name}: 返回的不是 PDF（可能被反爬或需要 cookie）"
                continue
            data = r.content
            if len(data) < 10000:
                last_err = f"{name}: 文件过小 ({len(data)}B)"
                continue
            sha = hashlib.sha256(data).hexdigest()
            dup = sha in seen_hashes
            path = _target_path(rec, cfg, dup=dup)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            state.set(rec.id, {
                "status": "ok",
                "path": str(path.relative_to(cfg.output_dir)),
                "sha256": sha,
                "source": name,
                "url": url,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            seen_hashes.add(sha)
            if dup:
                log.info("内容重复（与已下载文件相同）: %s", rec.title)
            return "ok"
    state.set(rec.id, {
        "status": "missing",
        "error": last_err or "无可用来源",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    return "missing"
