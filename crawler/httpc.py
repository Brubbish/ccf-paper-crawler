"""HTTP 层：每域名限速、cookie 注入（手工字符串 / Netscape cookies.txt）、可选 curl_cffi 浏览器指纹伪装。"""
from __future__ import annotations

import json
import logging
import pathlib
import threading
import time
import urllib.parse

import httpx

from .config import Config

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
}


class HttpError(RuntimeError):
    pass


class HttpResult:
    def __init__(self, status_code: int, headers: dict, content: bytes, url: str):
        self.status_code = status_code
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.content = content
        self.url = url

    @property
    def is_pdf(self) -> bool:
        return self.content[:5] == b"%PDF-"

    def json(self):
        return json.loads(self.content)


class Throttle:
    def __init__(self, intervals: dict):
        self.intervals = intervals
        self.lock = threading.Lock()
        self.next: dict[str, float] = {}

    def wait(self, host: str) -> None:
        iv = float(self.intervals.get(host, self.intervals.get("default", 0.6)))
        with self.lock:
            now = time.monotonic()
            t = max(now, self.next.get(host, 0.0))
            self.next[host] = t + iv
        rest = t - time.monotonic()
        if rest > 0:
            time.sleep(rest)

    def penalize(self, host: str, seconds: float) -> None:
        """限流惩罚：让该域名所有线程冷却一段时间（如 IEEE 420）。"""
        with self.lock:
            now = time.monotonic()
            self.next[host] = max(self.next.get(host, now), now + seconds)


def parse_cookie_string(s: str) -> dict:
    out = {}
    for part in (s or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def parse_cookie_file(path) -> dict:
    """自动识别三种格式：Cookie-Editor JSON 导出 / Netscape cookies.txt / 原始 Cookie 头字符串。"""
    p = pathlib.Path(path)
    if not p.exists():
        raise HttpError(f"cookie 文件不存在: {p}")
    text = p.read_text(encoding="utf-8", errors="replace").strip()
    if text.startswith("["):
        out = {}
        for c in json.loads(text):
            name, value = c.get("name"), c.get("value")
            # "_remove_" 是扩展导出的已删除 cookie 占位
            if name and value and value != "_remove_":
                out[name] = value
        return out
    if text.startswith("#") or "\t" in text:
        out = {}
        for line in text.splitlines():
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7 and parts[5] and parts[6]:
                out[parts[5]] = parts[6]
        return out
    return parse_cookie_string(text)


class Http:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.throttle = Throttle(cfg.min_interval)
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(90.0, connect=20.0),
            headers=BASE_HEADERS,
        )
        self._cookies: dict[str, dict] = {}
        for host, raw in (cfg.cookies or {}).items():
            if raw:
                self._cookies[host.lower()] = parse_cookie_string(raw)
        for host, path in (cfg.cookie_files or {}).items():
            if path:
                self._cookies.setdefault(host.lower(), {}).update(parse_cookie_file(path))
        self.impersonate = {h.lower() for h in (cfg.impersonate_domains or [])}
        self._creq = None
        if self.impersonate:
            try:
                from curl_cffi import requests as creq

                self._creq = creq
            except ImportError:
                print("[警告] 未安装 curl_cffi，ACM DL/IEEE 等站点可能因反爬 403，"
                      "建议: pip install curl_cffi")
        self._cf_lock = threading.Lock()
        self._cf_done: set = set()
        # ACM 的 Cloudflare 绑定 TLS 指纹且对 XHR 一律拦截（导航放行）；
        # 触发挑战后切换 nodriver 常驻 Chrome 会话用导航+download=true 下载（见 nodriver_dl）
        self._browser = None
        self._browser_hosts: set = set()
        self._browser_lock = threading.Lock()

    def _cookie_header(self, host: str) -> str:
        jar = self._cookies.get(host) or self._cookies.get(host.removeprefix("www.")) or {}
        return "; ".join(f"{k}={v}" for k, v in jar.items())

    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            tries: int = 4) -> HttpResult:
        if params:
            sep = "&" if "?" in url else "?"
            url = url + sep + urllib.parse.urlencode(params)
        host = urllib.parse.urlparse(url).netloc.lower()
        last: Exception | str | None = None
        for attempt in range(tries):
            self.throttle.wait(host)
            try:
                if host in self.impersonate and self._creq is not None:
                    if host in self._browser_hosts:
                        res = self._browser_get(url)
                    else:
                        res = self._curl_get(url, host, headers)
                else:
                    h = {}
                    ck = self._cookie_header(host)
                    if ck:
                        h["Cookie"] = ck
                    if headers:
                        h.update(headers)
                    resp = self.client.get(url, headers=h)
                    res = HttpResult(resp.status_code, dict(resp.headers), resp.content, str(resp.url))
                # 重试与限流退避对两条路径统一生效
                if res.status_code == 420:
                    self.throttle.penalize(host, 60.0)
                    logging.getLogger("http").warning("IEEE 限流(420)，%s 域名冷却 60s", host)
                if res.status_code == 403 and "dl.acm.org" in host:
                    # ACM 的挑战对非浏览器指纹是持续性的：直接切换常驻浏览器会话下载
                    if self._enable_browser(host):
                        continue  # 已切换浏览器通道，重试本次请求
                    self.throttle.penalize(host, 600.0)
                    logging.getLogger("http").warning("%s 返回 403（WAF 挑战页），域名冷却 600s", host)
                if res.status_code in (420, 403, 429, 500, 502, 503, 504) and attempt < tries - 1:
                    ra = res.headers.get("retry-after", "")
                    if res.status_code == 403:
                        base = 30.0
                    elif res.status_code == 420:
                        base = 12.0
                    else:
                        base = 3.0
                    time.sleep(min(float(ra), 30.0) if ra.replace(".", "").isdigit() else base * (attempt + 1))
                    last = f"HTTP {res.status_code}"
                    continue
                return res
            except Exception as e:  # httpx.TransportError / curl_cffi errors
                last = e
                if attempt < tries - 1:
                    time.sleep(2.0 * (attempt + 1))
        raise HttpError(f"GET {url} 失败（{tries} 次）: {last}")

    def _enable_browser(self, host: str) -> bool:
        """为 host（目前仅 dl.acm.org）启动 nodriver 常驻 Chrome 会话。"""
        with self._browser_lock:
            if host in self._browser_hosts:
                return True
            try:
                from .nodriver_dl import NodriverSession

                self._browser = NodriverSession(host)
                self._browser.start_background()
                self._browser_hosts.add(host)
                logging.getLogger("http").info("%s 已切换 nodriver 浏览器会话下载", host)
                return True
            except Exception as e:
                logging.getLogger("http").warning("nodriver 会话启动失败: %s", e)
                return False

    def _browser_get(self, url: str) -> HttpResult:
        return self._browser.get(url)

    def close_browser(self):
        if self._browser is not None:
            self._browser.close()
            self._browser = None

    def _curl_get(self, url: str, host: str, headers: dict | None) -> HttpResult:
        h = dict(BASE_HEADERS)
        ck = self._cookie_header(host)
        if ck:
            h["Cookie"] = ck
        if headers:
            h.update(headers)
        r = self._creq.get(url, headers=h, impersonate="chrome", timeout=90, allow_redirects=True)
        return HttpResult(r.status_code, dict(r.headers), r.content, str(r.url))
