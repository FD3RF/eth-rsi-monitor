#!/usr/bin/env python3
"""
旺财 - 数据层 (data.py)
==================
职责: 所有外部API数据获取
SSOT: K线数据是唯一价格来源，其他为参考上下文
"""
import requests
from typing import Tuple, Dict, Optional

# ===================== API配置 =====================
GATEIO = "https://api.gateio.ws/api/v4/futures/usdt"
TG_TOKEN = "8640664409:AAHPZIZd1YGa6jCwXziM01qoJ0RJwCfG-LM"
TG_CHAT  = "8410098965"
BARK_KEY = "gbRTde9uu3C8AwZBqorEj8"

# ===================== 核心K线 =====================
def get_candles(interval: str, limit: int) -> list:
    """
    获取K线数据（唯一价格来源）
    Args:
        interval: 15m / 1h / 4h
        limit: 蜡烛数量
    Returns:
        [{"c":close, "h":high, "l":low, "v":volume}, ...]
    """
    try:
        r = requests.get(
            f"{GATEIO}/candlesticks",
            params={"contract": "ETH_USDT", "interval": interval, "limit": limit},
            timeout=8
        )
        if r.status_code != 200:
            return []
        d = r.json()
        d.reverse()
        return [{"c": float(x["c"]), "h": float(x["h"]),
                 "l": float(x["l"]), "v": float(x["v"])} for x in d]
    except:
        return []


# ===================== 当前价格 =====================
def get_price() -> Tuple[float, float, float, float]:
    """
    获取当前价格和24h数据
    Returns: (last_price, volume_24h, low_24h, high_24h)
    """
    try:
        r = requests.get(f"{GATEIO}/tickers?contract=ETH_USDT", timeout=8)
        if r.status_code == 200:
            d = r.json()[0]
            return (float(d["last"]), float(d["volume_24h"]),
                    float(d["low_24h"]), float(d["high_24h"]))
    except:
        pass
    return 0, 0, 0, 0


# ===================== 清算数据（参考层） =====================
def get_liquidation() -> Dict:
    """
    获取多空清算数据（用于结构分析，不参与决策）
    Returns:
        {
          "tl": 多头清算总量(USD),
          "ts": 空头清算总量(USD),
          "lc": [{"p":价格, "a":金额}, ...],  # 多头密集
          "sc": [{"p":价格, "a":金额}, ...]   # 空头密集
        }
    """
    try:
        r = requests.get(
            "https://www.okx.com/api/v5/public/liquidation-orders",
            params={"instType": "SWAP", "instFamily": "ETH-USDT",
                    "state": "filled", "limit": "50"},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200:
            return {}
        data = r.json().get("data", [])
        lo = {}; so = {}; tl = 0; ts = 0
        for item in data:
            for d in item.get("details", []):
                px = round(float(d["bkPx"]) / 5) * 5
                usd = float(d["sz"]) * px
                if d["posSide"] == "long":
                    lo[px] = lo.get(px, 0) + usd; tl += usd
                else:
                    so[px] = so.get(px, 0) + usd; ts += usd
        return {
            "tl": round(tl), "ts": round(ts),
            "lc": [{"p": p, "a": round(a)} for p, a in
                   sorted(lo.items(), key=lambda x: -x[1])[:3]],
            "sc": [{"p": p, "a": round(a)} for p, a in
                   sorted(so.items(), key=lambda x: -x[1])[:3]]
        }
    except:
        return {}


# ===================== 资金费率 =====================
def get_funding_rate() -> str:
    """获取资金费率（展示用）"""
    try:
        r = requests.get(f"{GATEIO}/funding_rate?contract=ETH_USDT", timeout=8)
        if r.status_code == 200:
            fr = float(r.json()[0]["funding_rate"])
            return f"{'正' if fr > 0 else '负'}{abs(fr) * 100:.4f}%"
    except:
        pass
    return "未知"


# ===================== OI（未平合约） =====================
def get_oi() -> str:
    """获取OI总量"""
    try:
        r = requests.get(
            "https://www.okx.com/api/v5/public/open-interest",
            params={"instType": "SWAP", "instFamily": "ETH-USDT"},
            timeout=8
        )
        if r.status_code == 200:
            d = r.json()["data"][0]
            return f"${round(float(d['oiUsd']) / 1e6)}M"
    except:
        pass
    return "-"


def get_oi_volume() -> str:
    """获取OI + Volume"""
    try:
        r = requests.get(
            "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume",
            params={"ccy": "ETH", "period": "1H"},
            timeout=8
        )
        if r.status_code == 200:
            d = r.json().get("data", [])
            if d:
                oi = round(float(d[0][1]) / 1e6)
                vol = round(float(d[0][2]) / 1e6)
                return f"${oi}M/${vol}M"
    except:
        pass
    return "-/-"


# ===================== 订单簿深度 =====================
def get_depth() -> str:
    """获取买卖盘深度（展示用）"""
    try:
        r = requests.get(
            f"{GATEIO}/order_book?contract=ETH_USDT&limit=10",
            timeout=8
        )
        if r.status_code == 200:
            dd = r.json()
            bs = sum(float(b[1]) * float(b[0])
                     for b in dd.get("bids", []))
            as_ = sum(float(a[1]) * float(a[0])
                      for a in dd.get("asks", []))
            if bs + as_ > 0:
                return f"买${bs/1e6:.2f}M/卖${as_/1e6:.2f}M"
    except:
        pass
    return "未知"


# ===================== 多空比 =====================
def get_long_short_ratio() -> str:
    """
    获取多空比（币安 + OKX）
    用途: 参与 state_machine 判断 ls_extreme
    """
    try:
        r = requests.get(
            "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
            params={"symbol": "ETHUSDT", "period": "1h", "limit": 1},
            timeout=8
        )
        g = round(float(r.json()[0]["longShortRatio"]), 2) \
            if r.status_code == 200 else "-"

        r2 = requests.get(
            "https://fapi.binance.com/futures/data/topLongShortPositionRatio",
            params={"symbol": "ETHUSDT", "period": "1h", "limit": 1},
            timeout=8
        )
        t = round(float(r2.json()[0]["longShortRatio"]), 2) \
            if r2.status_code == 200 else "-"

        r3 = requests.get(
            "https://fapi.binance.com/futures/data/takerlongshortRatio",
            params={"symbol": "ETHUSDT", "period": "1h", "limit": 1},
            timeout=8
        )
        tk = round(float(r3.json()[0]["buySellRatio"]), 2) \
            if r3.status_code == 200 else "-"

        r4 = requests.get(
            "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio",
            params={"ccy": "ETH", "period": "1H"},
            timeout=8
        )
        ok = round(float(r4.json()["data"][0][1]), 2) \
            if r4.status_code == 200 and r4.json().get("data") else "-"

        return f"币安{g}/大户{t}/吃单{tk} OKX{ok}"
    except:
        return "-/-"


# ===================== 推送通知 =====================
def push(title: str, body: str):
    """
    推送消息到 Telegram + Bark
    """
    for _ in range(2):
        try:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={"chat_id": TG_CHAT, "text": title[:2000]},
                timeout=10
            )
            requests.post(
                "https://api.day.app/push",
                json={"device_key": BARK_KEY, "title": title[:50],
                      "body": body[:200], "group": "旺财",
                      "level": "timeSensitive", "badge": 1},
                timeout=8
            )
            return
        except:
            pass
