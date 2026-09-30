"""CCF-A 安全/软工论文批量 PDF 爬取。

用法示例：
  python -m crawler --list
  python -m crawler --venue CCS --year 2024 --limit 5          # 试跑
  python -m crawler --area security                            # 整个领域、config.yaml 里的年份
  python -m crawler --all                                      # 全部领域
  python -m crawler --venue TSE --year 2024 --retry-missing    # 重试上次未获取的
"""
from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import metadata
from .config import DEFAULT_CONFIG, load_config
from .download import State, process_record
from .httpc import Http
from .registry import VENUES, find_venue
from .report import write_reports
from .resolve import Resolver


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="crawler", description="CCF-A 安全/软工论文批量 PDF 爬取")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="config.yaml 路径")
    p.add_argument("--area", action="append", choices=["security", "software-engineering"],
                   help="限定领域，可多次")
    p.add_argument("--venue", action="append", help="限定会议/期刊（如 CCS、TSE），可多次")
    p.add_argument("--year", action="append", help="限定年份，可多次或逗号分隔（默认取 config.yaml 的 years）")
    p.add_argument("--limit", type=int, help="每个 venue-year 最多处理 N 篇（试跑用）")
    p.add_argument("--list", action="store_true", help="列出支持的 venue")
    p.add_argument("--metadata-only", action="store_true", help="只采集元数据，不下载")
    p.add_argument("--refresh-metadata", action="store_true", help="忽略元数据缓存，重新采集")
    p.add_argument("--retry-missing", action="store_true", help="重试上次标记 missing 的论文")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def selections(args, cfg):
    years = []
    for y in (args.year or [str(v) for v in cfg.years]):
        years.extend(int(v) for v in str(y).split(",") if v.strip())
    if args.venue:
        specs = [find_venue(v) for v in args.venue]
    elif args.area:
        specs = [s for s in VENUES.values() if s.area in args.area]
    else:
        specs = list(VENUES.values())
    seen, sel = set(), []
    for s in specs:
        for y in years:
            k = (s.key, y)
            if k not in seen:
                seen.add(k)
                sel.append((s, y))
    return sel


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg = load_config(args.config)

    if args.list:
        print(f"{'venue':<16} {'area':<22} kind        采集方式")
        for s in VENUES.values():
            print(f"{s.key:<16} {s.area:<22} {s.kind:<10} {s.collector}")
        return

    http = Http(cfg)
    cr = metadata.crossref.Crossref(http, cfg.unpaywall_email)
    resolver = Resolver(cfg, http)
    state = State(cfg.output_dir / "_state" / "state.json")
    sel = selections(args, cfg)
    if not sel:
        print("没有选中任何 venue-year；用 --venue / --area / --year 指定，或 --list 查看支持列表")
        return
    print(f"计划处理 {len(sel)} 个 venue-year：{', '.join(f'{s.key}{y}' for s, y in sel)}")

    totals = {"ok": 0, "missing": 0, "skip-ok": 0, "skip-missing": 0}
    interrupted = False
    for spec, year in sel:
        try:
            records = metadata.collect(spec, year, cfg, http, cr,
                                       refresh=args.refresh_metadata, limit=args.limit)
        except metadata.CollectorError as e:
            logging.warning("[%s %s] 跳过: %s", spec.key, year, e)
            continue
        if args.metadata_only:
            print(f"[{spec.key} {year}] 元数据 {len(records)} 条（--metadata-only，未下载）")
            continue

        todo = []
        for r in records:
            st = state.get(r.id)
            if st and st.get("status") == "ok" and (cfg.output_dir / st.get("path", "")).exists():
                totals["skip-ok"] += 1
            elif st and st.get("status") == "missing" and not args.retry_missing:
                totals["skip-missing"] += 1
            else:
                todo.append(r)
        if args.limit:
            todo = todo[:args.limit]
        print(f"[{spec.key} {year}] 共 {len(records)} 篇，本次下载 {len(todo)} 篇")

        seen_hashes = set()
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, cfg.workers)) as ex:
            futs = {ex.submit(process_record, r, cfg, http, resolver, state, seen_hashes,
                              args.retry_missing): r for r in todo}
            try:
                for fut in as_completed(futs):
                    r = futs[fut]
                    try:
                        outcome = fut.result()
                    except Exception as e:
                        logging.error("[%s %s] %s 处理异常: %s", spec.key, year, r.title[:50], e)
                        outcome = "missing"
                    totals[outcome] = totals.get(outcome, 0) + 1
                    done += 1
                    if outcome == "missing":
                        logging.info("[%s %s] 未获取: %s", spec.key, year, r.title[:70])
                    if done % 20 == 0 or done == len(todo):
                        print(f"[{spec.key} {year}] 进度 {done}/{len(todo)}")
            except KeyboardInterrupt:
                interrupted = True
                logging.warning("收到中断信号，正在保存进度……")
                ex.shutdown(wait=True, cancel_futures=True)
        if interrupted:
            break

    write_reports(cfg, state)
    try:
        http.close_browser()
    except Exception:
        pass
    ok = totals.get("ok", 0)
    print(f"\n完成：成功 {ok}，未获取 {totals.get('missing', 0)}，"
          f"跳过(已下载) {totals.get('skip-ok', 0)}，跳过(之前missing) {totals.get('skip-missing', 0)}")
    if totals.get("missing", 0):
        print("未获取的论文见 missing.csv（含 DOI 链接）；IEEE/ACM 付费论文配置 cookie 后用 --retry-missing 重试。")
    if interrupted:
        sys.exit(130)


if __name__ == "__main__":
    main()
