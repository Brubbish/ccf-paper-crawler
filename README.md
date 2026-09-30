# CCF-A Paper Crawler（安全 / 软工）

批量下载 **CCF-A 类安全与软件工程** 会议/期刊论文 PDF 的 Python 爬虫。
按 **领域 → 会议 → 年份** 三级目录组织，支持断点续传、多渠道 PDF 解析级联，并对 ACM DL / IEEE Xplore 的反爬（Cloudflare 挑战、HTTP 420 限流）内置自动对策。

**实测战绩（2026-09，四个月份 × 16 个 venue，约 1.5 万篇）**：平均成功率 **99%**（2024 年达 99.9%），其中正文占 96.4%，其余为出版社正式收录的 poster/demo 摘要与 workshop 前言。

```
2023: 2983/3000 (99.4%)   2024: 3331/3336 (99.9%)
2025: 4232/4305 (98.3%)   2026: 4073/4120 (98.9%)
security: 9063/9188 (98.6%)   software-engineering: 5556/5573 (99.7%)
```

## 覆盖范围

| 领域 | 会议 | 期刊 |
|---|---|---|
| `security` | IEEE S&P、ACM CCS、USENIX Security、NDSS | IEEE TIFS、IEEE TDSC |
| `software-engineering` | ICSE、FSE/ESEC、ASE、ISSTA、SOSP、PLDI、POPL | IEEE TSE、ACM TOSEM、TOPLAS |

## 渠道策略（按 venue 自动选择）

| 渠道 | venue | 方式 |
|---|---|---|
| 官网直下（100% 免费） | USENIX Security、NDSS | 解析官网论文页 → 直接 PDF 链接 |
| ACM DL（2026 起全 OA） | CCS、ICSE、FSE、ASE、ISSTA、SOSP、PLDI、POPL、TOSEM、TOPLAS | 内置 proceedings DOI 表 → Crossref 精确枚举 → `dl.acm.org/doi/pdf/{DOI}` |
| IEEE Xplore | SP、TIFS、TDSC、TSE 及 IEEE 出版的 ICSE/ASE 年份 | DOI → arnumber → `stampPDF/getPDF.jsp`（机构 cookie 可提高命中） |
| OA 兜底级联 | 全部 | Unpaywall → OpenAlex → Semantic Scholar → arXiv 标题匹配 |

## 安装

```bash
python -m pip install -r requirements.txt
```

- Python ≥ 3.10，Windows/Linux/macOS 均可（反爬兜底通道依赖本机已安装的 Chrome）。
- `curl_cffi` 用于模拟 Chrome TLS 指纹；`nodriver` 用于 Cloudflare 深度挑战时的真实浏览器下载。

## 使用

```bash
python -m crawler --list                                        # 查看支持的 venue
python -m crawler --venue USENIX-Security --year 2024 --limit 5 # 试跑 5 篇
python -m crawler --area security                               # 一个领域 × config 年份
python -m crawler --year 2023,2024                              # 指定年份全量
python -m crawler --all                                         # 全部
python -m crawler --venue TSE --year 2024 --metadata-only       # 只采集元数据
python -m crawler --all --retry-missing                         # 补漏（配好 cookie 后）
python -m crawler --venue CCS --year 2024 --refresh-metadata    # 强制重采元数据
```

随时 Ctrl+C 中断，进度保存在 `output/_state/state.json`，重跑自动跳过已下载文件。

## 配置（config.yaml）

```yaml
output_dir: D:\Papers\output
unpaywall_email: you@example.com     # 改成真实邮箱，Unpaywall 兜底才生效
years: [2023, 2024, 2025, 2026]
workers: 3                           # 每域名另有独立限速
exclude_patterns: [poster, demo, keynote, panel, "table of contents", ...]
min_interval:                        # 每域名最小请求间隔（秒）
  dl.acm.org: 8.0
  ieeexplore.ieee.org: 6.0
cookies:                             # 方式一：直接粘贴 Cookie 头字符串
  ieeexplore.ieee.org: ""
cookie_files:                        # 方式二：浏览器扩展导出的文件
  ieeexplore.ieee.org: D:/Papers/ieee.txt   # 支持 Cookie-Editor JSON / Netscape txt / 原始串
```

IEEE/Springer 付费论文**强烈建议配置机构订阅 cookie**（浏览器登录 Xplore 后导出）。
无 cookie 也能跑：拿不到的进 `missing.csv`（含 DOI 链接可手动补）。

## 输出

```
output/
├── security\CCS\2024\Wang_Some-Paper-Title.pdf      # {领域}\{会议}\{年份}\{第一作者}_{标题}.pdf
├── software-engineering\ICSE\2023\...
├── manifest.csv      # 全量清单：标题/作者/DOI/状态/来源渠道/本地路径
├── missing.csv       # 未获取清单（含 DOI 链接，便于手动补）
└── _state/           # 断点续传状态 + 元数据缓存
```

## 反爬对策（实测经验，详见代码注释）

- **IEEE HTTP 420（限流）**：6s/篇 + 命中后 60s 域名冷却 + 指数退避；瞬时失败靠 `--retry-missing` 多轮回收。
- **ACM Cloudflare "Just a moment"**：批量爬取几分钟后触发并对出口 IP 持续标记。此时 TLS 指纹伪装（curl_cffi）、注入 `cf_clearance`、页内 fetch **全部无效**（CF 绑定 TLS/HTTP2 指纹；导航请求放行、XHR 一律拦截）。对策：
  1. 元数据不走 ACM 页面，全部来自 Crossref（内置 proceedings DOI 表精确枚举）；
  2. 下载被 403 时自动切换 **nodriver 常驻 Chrome 会话**——真实浏览器导航 + `?download=true` 强制附件下载，从下载目录捕获原始字节；偶发“真人验证”复选框由 `verify_cf()` 自动点击。
  3. 运行时会弹一个最小化的 Chrome 窗口，属正常现象。
- **错误卷防护**：ACM 会议的 Crossref 模糊检索会被同名子串卷污染（如 CCS 会命中 Asia CCS），故 DOI 表里没有的年份直接按“未出版”跳过。

## 项目结构

```
crawler/
├── main.py            # CLI 与调度
├── config.py          # 配置加载
├── registry.py        # venue 注册表（含各会议 proceedings DOI 表）
├── models.py          # PaperRecord
├── httpc.py           # HTTP 层：限速/重试/cookie/浏览器指纹/403 自动切换
├── resolve.py         # PDF 候选级联：直链→出版社→Unpaywall→OpenAlex→S2→arXiv
├── download.py        # 断点续传、内容去重、落盘命名
├── report.py          # manifest.csv / missing.csv
├── nodriver_dl.py     # ACM Cloudflare 兜底：nodriver 真实 Chrome 导航下载
└── metadata/
    ├── crossref.py    # Crossref API（期刊 ISSN / 会议卷精确枚举）
    ├── acm.py         # ACM proceedings DOI → 卷名 → 文章枚举
    ├── usenix.py      # USENIX 官网解析
    └── ndss.py        # NDSS 官网解析
```

## 合规

- 仅使用出版社官方渠道、开放获取镜像与你自己的订阅权限；不使用 Sci-Hub。
- cookie 仅用于本人合法订阅，请勿分享或提交到仓库。
- 请求间隔保守（IEEE 6s、ACM 8s），请勿调低；下载内容版权归出版社/作者，仅供个人研究使用。

## 已知限制

- 2026 年尚未出版的卷（CCS/ASE/ISSTA 2026 等）会在出版后需在 `registry.py` 补一行 proceedings DOI。
- 约 3.6% 的文件是出版社正式收录的 poster/demo 摘要与 workshop 前言（1-4 页），标题与 DOI 完整，可按文件名辨识后自行剔除。
- IEEE 少量存量论文任何渠道均无开放副本，只能进 missing 清单。
