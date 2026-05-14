#!/usr/bin/env python3
"""
ETH 永续合约 RSI 监控 — REST 轮询版（不依赖 ccxt.pro）
逻辑: 每分钟检查一次15m K线是否闭合 → 计算多周期RSI → Bark推送
依赖: pip install aiohttp ccxt
"""

import asyncio
import aiohttp
import logging
from collections import deque
from typing import Dict, Set
from datetime import datetime, timezone
import sys
import urllib.parse
import json

# ========== 配置 ==========
SYMBOL = "ETH/USDT:USDT"
EXCHANGE_ID = "gateio"   # ccxt 正确 ID
BARK_URL = "https://api.day.app/gbRTde9uu3C8AwZBqorEj8/"

OVERSOLD = [10, 20, 30]
OVERBOUGHT = [70, 80, 90]
PERIODS = {"15m": 1, "1h": 4, "4h": 16}
RSI_PERIOD = 14
CHECK_INTERVAL = 60   # 每60秒检查一次

# ========== 日志 ==========
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/root/eth_monitor/logs/rsi_swap.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("RSI")


# ========== RSI 计算 ==========
def rsi_wilder(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(diff if diff > 0 else 0.0)
        losses.append(-diff if diff < 0 else 0.0)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


# ========== 多周期状态机 ==========
class RsiMonitor:
    def __init__(self):
        self.candles = deque(maxlen=800)
        self._pushed: Dict[str, Set[int]] = {tf: set() for tf in PERIODS}

    def feed_closed(self, ts: int, close: float) -> bool:
        if self.candles and self.candles[-1][0] == ts:
            return False
        self.candles.append((ts, close))
        return True

    def get_rsi(self, tf: str) -> float:
        multiplier = PERIODS[tf]
        total = len(self.candles)
        if total < multiplier * RSI_PERIOD:
            return 50.0
        aggregated = []
        for start in range(total - multiplier, -1, -multiplier):
            aggregated.append(self.candles[start + multiplier - 1][1])
        aggregated.reverse()
        if len(aggregated) < RSI_PERIOD:
            return 50.0
        return rsi_wilder(aggregated[-RSI_PERIOD - 1:])

    async def alert_if_needed(self, tf: str, rsi: float, price: float, notifier):
        triggers = set()
        if rsi <= max(OVERSOLD):
            triggers = {t for t in OVERSOLD if rsi <= t}
        elif rsi >= min(OVERBOUGHT):
            triggers = {t for t in OVERBOUGHT if rsi >= t}
        else:
            if self._pushed[tf]:
                self._pushed[tf].clear()
            return

        new_triggers = triggers - self._pushed[tf]
        if not new_triggers:
            return

        direction = "超卖" if rsi <= max(OVERSOLD) else "超买"
        op = "<=" if direction == "超卖" else ">="
        for t in sorted(new_triggers):
            title = f"ETH {tf} RSI={rsi:.1f} {direction}"
            content = f"价格 {price:.2f}\nRSI 阈值 {op} {t}"
            await notifier.send(title, content)
            self._pushed[tf].add(t)


# ========== Bark 推送 ==========
class Bark:
    def __init__(self, url, retries=3):
        self.url = url.rstrip("/")
        self.retries = retries

    async def send(self, title, body):
        encoded = f"{self.url}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}?level=timesensitive"
        for i in range(self.retries):
            try:
                timeout = aiohttp.ClientTimeout(total=8)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(encoded) as resp:
                        if resp.status == 200:
                            return
                        logger.warning(f"Bark HTTP {resp.status}")
            except Exception as e:
                logger.error(f"Bark异常: {e}")
            await asyncio.sleep(2 ** i)
        logger.error("Bark推送彻底失败")


# ========== 数据拉取（纯 REST，不依赖 ccxt.pro）==========
class DataFetcher:
    def __init__(self, exchange_id: str):
        self.exchange_id = exchange_id
        self.last_ts = 0

    async def _sync_fetch(self, limit=300, since_ms=None):
        def sync():
            import ccxt
            ex = getattr(ccxt, self.exchange_id)({
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })
            try:
                if since_ms:
                    ohlcv = ex.fetch_ohlcv(SYMBOL, "15m", since=since_ms, limit=limit)
                else:
                    ohlcv = ex.fetch_ohlcv(SYMBOL, "15m", limit=limit)
                return ohlcv
            finally:
                ex.close()
        return await asyncio.to_thread(sync)

    async def load_history(self):
        logger.info("加载历史K线...")
        ohlcv = await self._sync_fetch(limit=300)
        result = [(c[0] // 1000, c[4]) for c in ohlcv]
        if result:
            self.last_ts = result[-1][0]
            logger.info(f"历史载入 {len(result)} 根，最新 {datetime.fromtimestamp(self.last_ts, tz=timezone.utc)}")
        return result

    async def fetch_new(self):
        """拉取 last_ts 之后的新K线"""
        if self.last_ts == 0:
            return []
        ohlcv = await self._sync_fetch(limit=50, since_ms=(self.last_ts + 1) * 1000)
        new = [(c[0] // 1000, c[4]) for c in ohlcv if (c[0] // 1000) > self.last_ts]
        if new:
            self.last_ts = new[-1][0]
        return new

    async def fetch_latest(self):
        """拉取最新一根K线（用于判断当前价格）"""
        ohlcv = await self._sync_fetch(limit=1)
        if ohlcv:
            return (ohlcv[0][0] // 1000, ohlcv[0][4])
        return None


# ========== 主程序 ==========
async def main():
    logger.info(f"启动 ETH 永续合约 RSI 监控 → {EXCHANGE_ID} {SYMBOL}")
    rsi_mon = RsiMonitor()
    bark = Bark(BARK_URL)
    fetcher = DataFetcher(EXCHANGE_ID)

    # 加载历史
    hist = await fetcher.load_history()
    for ts, close in hist:
        rsi_mon.feed_closed(ts, close)

    logger.info(f"开始监控，每 {CHECK_INTERVAL}s 检查一次...")

    while True:
        try:
            # 拉取新闭合的K线
            new_candles = await fetcher.fetch_new()
            for ts, close in new_candles:
                if not rsi_mon.feed_closed(ts, close):
                    continue
                dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M")
                logger.info(f"新闭合K线 {dt} 价格={close:.2f}")
                for tf in PERIODS:
                    rsi = rsi_mon.get_rsi(tf)
                    await rsi_mon.alert_if_needed(tf, rsi, close, bark)

            # 即使没有新K线，也检查当前价格（用于日志）
            if not new_candles:
                latest = await fetcher.fetch_latest()
                if latest:
                    ts, price = latest
                    dt_now = datetime.now(tz=timezone.utc).strftime("%H:%M:%S")
                    # 每10分钟打一次日志（避免刷屏）
                    if ts % 600 < CHECK_INTERVAL:
                        rsi_15m = rsi_mon.get_rsi("15m")
                        logger.info(f"[{dt_now}] 当前价={price:.2f} RSI15m={rsi_15m:.1f}")

        except Exception as e:
            logger.exception(f"主循环异常: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("监控已手动停止")
