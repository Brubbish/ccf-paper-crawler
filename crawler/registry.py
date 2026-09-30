"""Venue registry: CCF-A security + software engineering venues (CRYPTO/EUROCRYPT/OSDI excluded per user)."""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class VenueSpec:
    key: str                      # 目录名，如 CCS / USENIX-Security
    area: str                     # security | software-engineering
    kind: str                     # conference | journal
    collector: str                # usenix | ndss | crossref_journal | crossref_journal_issue | acm_proceedings | crossref_proceedings
    # crossref_journal
    issn: str | None = None
    journal_query: str | None = None          # ISSN 运行时发现（Crossref journals 检索）
    # crossref_journal_issue（PACM* 期刊型会议）
    issue_tag: str | None = None              # 如 POPL / PLDI / FSE
    issue_from_year: int | None = None        # 该年份起走 journal_issue 采集
    volume_base_year: int | None = None       # 该年对应 PACM 第 1 卷
    # acm_proceedings / crossref_proceedings
    proc_doi: dict = dataclasses.field(default_factory=dict)   # 年份 -> ACM DL proceedings DOI
    proc_queries: list | None = None          # 无 DOI 时 container-title 的检索词（尽力而为）


VENUES: dict[str, VenueSpec] = {}


def _add(spec: VenueSpec) -> None:
    VENUES[spec.key.lower()] = spec


# ---------- 安全 ----------
_add(VenueSpec(key="SP", area="security", kind="conference", collector="crossref_proceedings",
               proc_queries=["IEEE Symposium on Security and Privacy"]))
_add(VenueSpec(key="CCS", area="security", kind="conference", collector="acm_proceedings",
               proc_doi={2022: "10.1145/3548606", 2023: "10.1145/3576915",
                         2024: "10.1145/3658644", 2025: "10.1145/3719027"},
               proc_queries=["ACM SIGSAC Conference on Computer and Communications Security",
                             "Conference on Computer and Communications Security"]))
_add(VenueSpec(key="USENIX-Security", area="security", kind="conference", collector="usenix"))
_add(VenueSpec(key="NDSS", area="security", kind="conference", collector="ndss"))
_add(VenueSpec(key="TIFS", area="security", kind="journal", collector="crossref_journal", issn="1556-6013"))
_add(VenueSpec(key="TDSC", area="security", kind="journal", collector="crossref_journal", issn="1545-5971"))

# ---------- 软件工程 ----------
_add(VenueSpec(key="ICSE", area="software-engineering", kind="conference", collector="acm_proceedings",
               proc_doi={2022: "10.1145/3510454",
                         2023: "10.1109/icse48619.2023",   # IEEE 出版年，ACM DL 挂 10.5555 内部号
                         2024: "10.1145/3597503",
                         2025: "10.1109/icse55347.2025",
                         2026: "10.1145/3744916"},
               proc_queries=["International Conference on Software Engineering"]))
_add(VenueSpec(key="FSE", area="software-engineering", kind="conference", collector="crossref_journal_issue",
               journal_query="Proceedings of the ACM on Software Engineering", issue_tag="FSE",
               issue_from_year=2024, volume_base_year=2024,   # FSE 2024 起转 PACMSE 期刊制
               proc_doi={2022: "10.1145/3540250", 2023: "10.1145/3611643"},
               proc_queries=["European Software Engineering Conference",
                             "Symposium on the Foundations of Software Engineering"]))
_add(VenueSpec(key="ASE", area="software-engineering", kind="conference", collector="acm_proceedings",
               proc_doi={2022: "10.1145/3551349",
                         2023: "10.1109/ase56229.2023",
                         2024: "10.1145/3691620",
                         2025: "10.1109/ASE63991.2025"},
               proc_queries=["International Conference on Automated Software Engineering",
                             "Automated Software Engineering"]))
_add(VenueSpec(key="ISSTA", area="software-engineering", kind="conference", collector="acm_proceedings",
               proc_doi={2022: "10.1145/3533767", 2023: "10.1145/3597926",
                         2024: "10.1145/3650212", 2025: "10.1145/3713081"},
               proc_queries=["International Symposium on Software Testing and Analysis"]))
_add(VenueSpec(key="SOSP", area="software-engineering", kind="conference", collector="acm_proceedings",
               proc_doi={2023: "10.1145/3600006", 2024: "10.1145/3694715", 2025: "10.1145/3731569"},
               proc_queries=["Symposium on Operating Systems Principles"]))
_add(VenueSpec(key="PLDI", area="software-engineering", kind="conference", collector="crossref_journal_issue",
               journal_query="Proceedings of the ACM on Programming Languages", issn="2475-1421",
               issue_tag="PLDI", issue_from_year=2017, volume_base_year=2017,
               proc_queries=["Conference on Programming Language Design and Implementation"]))
_add(VenueSpec(key="POPL", area="software-engineering", kind="conference", collector="crossref_journal_issue",
               journal_query="Proceedings of the ACM on Programming Languages", issn="2475-1421",
               issue_tag="POPL", issue_from_year=2017, volume_base_year=2017,
               proc_queries=["Symposium on Principles of Programming Languages"]))
_add(VenueSpec(key="TSE", area="software-engineering", kind="journal", collector="crossref_journal", issn="0098-5589"))
_add(VenueSpec(key="TOSEM", area="software-engineering", kind="journal", collector="crossref_journal", issn="1049-331X"))
_add(VenueSpec(key="TOPLAS", area="software-engineering", kind="journal", collector="crossref_journal",
               issn="0164-0925", journal_query="Transactions on Programming Languages and Systems"))

ALIASES = {
    "oakland": "sp",
    "ieee-sp": "sp",
    "usenix": "usenix-security",
    "usenixsec": "usenix-security",
    "sec": "usenix-security",
    "esec/fse": "fse",
    "esec-fse": "fse",
}


def find_venue(name: str) -> VenueSpec:
    key = name.strip().lower()
    key = ALIASES.get(key, key)
    if key not in VENUES:
        raise SystemExit(f"未知 venue: {name}（可用: {', '.join(v.key for v in VENUES.values())}）")
    return VENUES[key]
