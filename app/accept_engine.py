import asyncio
import json
import threading
import time
from asyncio import Semaphore, Queue

import aiohttp

from . import config


class AcceptEngine:

    def __init__(self, token, add_bearer, concurrency=80, on_error=None):
        self._token = token
        self._add_bearer = add_bearer
        self._concurrency = concurrency
        self._on_error = on_error or (lambda msg: None)
        self._queue = Queue()
        self._loop = None
        self._thread = None
        self._started = False

    @property
    def token(self):
        return self._token

    @token.setter
    def token(self, v):
        self._token = v

    def start(self):
        if self._started:
            return
        self._started = True
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._started = False

    def enqueue(self, order_no, order_obj, source, on_ok, on_fail):
        if not self._loop or not self._loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(
            self._queue.put((order_no, order_obj, source, on_ok, on_fail)),
            self._loop,
        )

    def enqueue_batch(self, tasks, source, on_ok, on_fail):
        """整批入队，一次 call_soon_threadsafe 替代N次调用。"""
        if not self._loop or not self._loop.is_running():
            self._on_error(f"[接单引擎] 循环未运行，{len(tasks)} 个订单被丢弃")
            return
        batch = []
        for order_obj in tasks:
            order_no = order_obj.get("orderNo", "")
            if not order_no:
                continue
            batch.append((order_no, order_obj, source, on_ok, on_fail))
        if batch:
            asyncio.run_coroutine_threadsafe(
                self._queue.put(batch),
                self._loop,
            )

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._worker())
        except Exception as e:
            self._on_error(f"[接单引擎] 事件循环异常退出: {e}")
        self._loop.close()

    async def _worker(self):
        hall_sem = Semaphore(max(self._concurrency, 80))
        assign_sem = Semaphore(max(1, self._concurrency // 10))
        timeout = aiohttp.ClientTimeout(total=config.ACCEPT_TIMEOUT)
        connector = aiohttp.TCPConnector(limit=200)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            await self._warm_up(session)
            while True:
                item = await self._queue.get()
                if isinstance(item, list):
                    batch = item
                else:
                    batch = [item]
                    while not self._queue.empty():
                        try:
                            batch.append(self._queue.get_nowait())
                        except asyncio.QueueEmpty:
                            break
                tasks = [
                    self._accept_one(session,
                                     hall_sem if (t[1] or {}).get("status") == "pending" else assign_sem,
                                     *t)
                    for t in batch
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _accept_one(self, session, sem, order_no, order_obj, source,
                          on_ok, on_fail):
        async with sem:
            t0 = time.perf_counter()
            is_pending = (order_obj or {}).get("status") == "pending"
            endpoint = config.ENDPOINT_GRAB if is_pending else config.ENDPOINT_ACCEPT
            body = {"orderNo": order_no}
            try:
                url = config.API_BASE + endpoint
                headers = self._build_headers()
                async with session.post(url, json=body, headers=headers) as resp:
                    try:
                        result = await resp.json()
                    except Exception:
                        result = {"code": resp.status, "message": await resp.text()}
                    elapsed = int((time.perf_counter() - t0) * 1000)
                    if result.get("code") == 1000:
                        on_ok(order_no, order_obj, source, elapsed)
                    else:
                        on_fail(order_no, order_obj, source, result.get("message", ""), elapsed)
            except Exception as e:
                elapsed = int((time.perf_counter() - t0) * 1000)
                on_fail(order_no, order_obj, source, str(e), elapsed)

    async def _warm_up(self, session):
        """预热连接池，避免首单握手延迟。"""
        try:
            url = config.API_BASE + config.ENDPOINT_MY_ORDERS
            warm_tasks = [
                session.post(url, json={}, headers=self._build_headers())
                for _ in range(min(10, self._concurrency))
            ]
            await asyncio.gather(*warm_tasks, return_exceptions=True)
        except Exception:
            pass

    def _build_headers(self):
        t = self._token
        if self._add_bearer and t and not t.startswith("Bearer "):
            t = "Bearer " + t
        return {
            "Authorization": t,
            "Content-Type": "application/json",
            "language": "en",
            "Referer": "https://mix.easyflow.finance/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
        }
