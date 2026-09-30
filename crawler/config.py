from __future__ import annotations

import dataclasses
import pathlib

import yaml

DEFAULT_CONFIG = pathlib.Path(__file__).resolve().parent.parent / "config.yaml"

DEFAULT_MIN_INTERVALS = {
    "default": 0.6,
    "api.crossref.org": 0.35,
    "dl.acm.org": 2.0,
    "ieeexplore.ieee.org": 2.0,
    "link.springer.com": 1.5,
    "www.usenix.org": 0.7,
    "www.ndss-symposium.org": 0.5,
    "api.openalex.org": 0.15,
    "api.unpaywall.org": 0.15,
    "api.semanticscholar.org": 3.5,
    "export.arxiv.org": 3.2,
    "doi.org": 0.3,
}

DEFAULT_IMPERSONATE = [
    "dl.acm.org",
    "ieeexplore.ieee.org",
    "link.springer.com",
    "www.ndss-symposium.org",
    "www.usenix.org",
    "doi.org",
]

DEFAULT_EXCLUDE = ["poster", "demo", "keynote", "panel", "welcome", "closing remarks", "opening remarks"]


@dataclasses.dataclass
class Config:
    output_dir: pathlib.Path = pathlib.Path("output")
    unpaywall_email: str = ""
    years: list = dataclasses.field(default_factory=lambda: [2022, 2023, 2024, 2025, 2026])
    workers: int = 3
    exclude_patterns: list = dataclasses.field(default_factory=lambda: list(DEFAULT_EXCLUDE))
    fallbacks: dict = dataclasses.field(
        default_factory=lambda: {"unpaywall": True, "openalex": True, "s2": True, "arxiv": True}
    )
    cookies: dict = dataclasses.field(default_factory=dict)
    cookie_files: dict = dataclasses.field(default_factory=dict)
    min_interval: dict = dataclasses.field(default_factory=lambda: dict(DEFAULT_MIN_INTERVALS))
    impersonate_domains: list = dataclasses.field(default_factory=lambda: list(DEFAULT_IMPERSONATE))
    container_overrides: dict = dataclasses.field(default_factory=dict)
    acm_toc_check: bool = False   # dl.acm.org 目录页受 CF 严格防护且非必需，默认不抓


def load_config(path: pathlib.Path | None = None) -> Config:
    path = pathlib.Path(path) if path else DEFAULT_CONFIG
    raw: dict = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config()
    for key, value in raw.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    cfg.output_dir = pathlib.Path(cfg.output_dir)
    # 用户配置的限速叠加在默认值上（按域名覆盖）
    intervals = dict(DEFAULT_MIN_INTERVALS)
    intervals.update(raw.get("min_interval") or {})
    cfg.min_interval = intervals
    if raw.get("impersonate_domains") is not None:
        cfg.impersonate_domains = list(raw["impersonate_domains"])
    return cfg
