"""ACM 专用下载通道：nodriver 驱动真实 Chrome。

实测结论（2026-09，dl.acm.org 的 Cloudflare）：
- IP 被标记后，XHR/fetch 一律 403（无论是否在过挑战的浏览器内）；
- 但页面导航放行；且 `?download=true` 让 ACM 返回 Content-Disposition: attachment，
  Chrome 直接下载（绕开 PDF 查看器对响应体的替换），从下载目录拿原始字节。
- 偶发交互式挑战用 tab.verify_cf() 自动点击。
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
import urllib.parse

from .httpc import HttpResult

log = logging.getLogger("nodriver_dl")


class NodriverSession:
    """后台事件循环 + 单 Tab 导航下载。线程安全（外部线程经 get() 调用）。"""

    def __init__(self, host: str = "dl.acm.org"):
        self.host = host
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tab = None
        self._browser = None
        self._ready = threading.Event()
        self._dl_dir = tempfile.mkdtemp(prefix="acm_dl_")

    # ---------- 生命周期 ----------
    def start_background(self) -> None:
        t = threading.Thread(target=self._run_loop, daemon=True, name="nodriver")
        t.start()
        if not self._ready.wait(timeout=180):
            raise RuntimeError("nodriver 浏览器启动超时")

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start())
        self._loop.run_forever()

    async def _start(self) -> None:
        import nodriver as uc

        self._browser = await uc.start(
            headless=False,  # 有头最稳；窗口挪到屏幕外
            browser_args=["--window-position=3000,3000", "--start-minimized"],
        )
        self._tab = await self._browser.get(f"https://{self.host}/")
        await self._tab.set_download_path(self._dl_dir)
        html = await self._tab.get_content()
        if "just a moment" in html.lower():
            await self._verify(self._tab)
        self._ready.set()
        log.info("nodriver 会话就绪（host=%s, 下载目录=%s）", self.host, self._dl_dir)

    async def _verify(self, tab, tries: int = 3) -> None:
        for _ in range(tries):
            try:
                await tab.verify_cf()
            except Exception as e:
                log.debug("verify_cf: %s", e)
            await asyncio.sleep(5)
            html = await tab.get_content()
            if "just a moment" not in html.lower():
                return

    # ---------- 下载 ----------
    async def _get(self, url: str) -> HttpResult:
        from nodriver import cdp

        # /doi/pdf/ URL 强制附件下载，绕开 Chrome PDF 查看器
        if "/doi/pdf/" in url and "download" not in urllib.parse.parse_qs(
                urllib.parse.urlparse(url).query):
            url += ("&" if "?" in url else "?") + "download=true"

        before = set(os.listdir(self._dl_dir))
        await self._tab.send(cdp.page.navigate(url))
        got = None
        for _ in range(60):  # 最多等 30s
            await asyncio.sleep(0.5)
            new = set(os.listdir(self._dl_dir)) - before
            done = [f for f in new if not f.endswith((".crdownload", ".tmp", ".part"))]
            if done:
                got = done[0]
                break
        if not got:
            # 可能落在挑战页：尝试点击后放弃本次
            html = await self._tab.get_content()
            if "just a moment" in html.lower():
                await self._verify(self._tab)
            return HttpResult(403, {"content-type": "text/html"}, b"no download produced", url)
        path = os.path.join(self._dl_dir, got)
        try:
            data = open(path, "rb").read()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        if data[:5] == b"%PDF-":
            return HttpResult(200, {"content-type": "application/pdf"}, data, url)
        return HttpResult(403, {"content-type": "text/html"}, data[:200], url)

    def get(self, url: str, timeout: float = 120.0) -> HttpResult:
        if not self._ready.is_set():
            return HttpResult(0, {}, b"session not started", url)
        try:
            fut = asyncio.run_coroutine_threadsafe(self._get(url), self._loop)
            return fut.result(timeout=timeout)
        except Exception as e:
            log.warning("nodriver 下载失败: %s", e)
            return HttpResult(0, {}, str(e).encode(), url)

    def close(self) -> None:
        try:
            if self._browser:
                asyncio.run_coroutine_threadsafe(self._browser.stop(), self._loop).result(10)
        except Exception:
            pass
