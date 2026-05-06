#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETH RSI监控 V3.1 - 任意RSI触发即推送
- 低位: 任意RSI<30
- 高位: 任意RSI>70
- 仅30/70一组阈值
"""

import requests, time, threading
from datetime import datetime

GATEIO_API_URL = "https://api.gateio.ws/api/v4"
CONTRACT = "ETH_USDT"
BARK_KEY = "gbRTde9uu3C8AwZBqorEj8"
BARK_API_URL = "https://api.day.app/push"
RSI_PERIODS = [6, 14, 21]
LOW_THRESHOLD = 30
HIGH_THRESHOLD = 70
INTERVALS = {"15m": "15分钟", "1h": "1小时", "4h": "4小时"}
CHECK_INTERVAL = 60
REQUEST_TIMEOUT = 15

class CooldownSet:
    """基于时间的冷却去重，避免同一条件反复推送"""
    def __init__(self, cooldown_sec=1800):
        self._data = {}; self._lock = threading.Lock()
        self._cooldown = cooldown_sec  # 默认30分钟冷却
    def can_push(self, key):
        now = time.time()
        with self._lock:
            last = self._data.get(key, 0)
            if now - last < self._cooldown:
                return False
            self._data[key] = now
            # 清理过期数据，防止内存泄漏
            cutoff = now - self._cooldown * 2
            expired = [k for k, t in self._data.items() if t < cutoff]
            for k in expired: del self._data[k]
            return True

alert_cooldown = CooldownSet(cooldown_sec=1800)  # 同一条件30分钟内不重复推送

def calculate_rsi(prices, period):
    if not prices or len(prices) < period + 1: return None
    try:
        gains, losses = [], []
        for i in range(1, len(prices)):
            c = prices[i] - prices[i-1]
            if c > 0: gains.append(c); losses.append(0.0)
            else: gains.append(0.0); losses.append(abs(c))
        ag = sum(gains[:period]) / period
        al = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            ag = (ag * (period - 1) + gains[i]) / period
            al = (al * (period - 1) + losses[i]) / period
        if al < 1e-10: return 100.0
        return max(0.0, min(100.0, 100.0 - 100.0 / (1.0 + ag / al)))
    except: return None

def get_kline_data(interval):
    for a in range(3):
        try:
            r = requests.get("%s/futures/usdt/candlesticks" % GATEIO_API_URL,
                params={"contract": CONTRACT, "interval": interval, "limit": 100}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            d = r.json()
            if isinstance(d, list) and len(d) >= 30:
                return [float(c['c']) for c in d]
        except:
            if a < 2: time.sleep(2 ** a)
    return None

def push_bark(title, body):
    for a in range(3):
        try:
            r = requests.post(BARK_API_URL, json={
                "device_key": BARK_KEY, "title": title, "body": body,
                "level": "timeSensitive", "badge": 1
            }, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            if r.json().get("code") == 200:
                print("[%s] Bark推送成功" % datetime.now())
                return True
        except:
            if a < 2: time.sleep(1)
    return False

def monitor_period(interval, name):
    closes = get_kline_data(interval)
    if not closes or len(closes) < 22: return
    rsi = {}
    for p in RSI_PERIODS:
        v = calculate_rsi(closes, p)
        if v is not None: rsi[p] = v
    if len(rsi) < 3: return
    price = closes[-1]
    push_count = 0

    # 低位: 任意RSI < 30
    for p in RSI_PERIODS:
        if p in rsi and rsi[p] < LOW_THRESHOLD:
            key = "low_%s_%d" % (interval, p)
            if alert_cooldown.can_push(key):
                title = "ETH RSI低位告警"
                body = "[%s]\nRSI(6)=%.1f  RSI(14)=%.1f  RSI(21)=%.1f\n价格=$%.1f  %dh:%dm" % (
                    name, rsi[6], rsi[14], rsi[21], price,
                    datetime.now().hour, datetime.now().minute)
                if push_bark(title, body): push_count += 1

    # 高位: 任意RSI > 70
    for p in RSI_PERIODS:
        if p in rsi and rsi[p] > HIGH_THRESHOLD:
            key = "high_%s_%d" % (interval, p)
            if alert_cooldown.can_push(key):
                title = "ETH RSI高位告警"
                body = "[%s]\nRSI(6)=%.1f  RSI(14)=%.1f  RSI(21)=%.1f\n价格=$%.1f  %dh:%dm" % (
                    name, rsi[6], rsi[14], rsi[21], price,
                    datetime.now().hour, datetime.now().minute)
                if push_bark(title, body): push_count += 1

    parts = "  ".join(["RSI(%d)=%.2f" % (p, rsi.get(p, 0)) for p in RSI_PERIODS])
    extra = "  |  推送: %d条" % push_count if push_count else ""
    print("[%s] %s  |  %s%s" % (datetime.now(), name, parts, extra))

def main():
    print("="*60)
    print("ETH RSI监控 V3.1")
    print("时间: %s" % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print("合约: %s" % CONTRACT)
    print("RSI: %s" % RSI_PERIODS)
    print("低位: 任意RSI<%d  高位: 任意RSI>%d" % (LOW_THRESHOLD, HIGH_THRESHOLD))
    print("推送: Bark timeSensitive")
    print("="*60)
    while True:
        try:
            for iv, nm in INTERVALS.items():
                try: monitor_period(iv, nm)
                except Exception as e: print("[%s] %s异常: %s" % (datetime.now(), nm, e))
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt: break
        except Exception as e:
            print("[%s] 主循环异常: %s" % (datetime.now(), e))
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__": main()
