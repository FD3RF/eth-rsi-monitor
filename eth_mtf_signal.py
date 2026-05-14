#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
旺财 MTF Signal Service — 多周期趋势滚仓信号推送
=====================================================
核心逻辑：
  4H Regime（EMA144/169 趋势过滤）
  ↓
  1H Structure（Swing High/Low 自动识别）
  ↓
  15m Entry（结构突破/回踩信号）

推送规则：
  - 只在结构状态跃迁时推送（HH/HL/LL/LH 改变）
  - 无信号不推送（安静）
  - 不推送Exit

部署：
  systemd 服务，常驻后台每 60s 轮询
"""
import os, sys, json, time, csv
from datetime import datetime, timezone
from collections import defaultdict

# ── 配置 ──────────────────────────────────────────────────
SYMBOL      = "ETHUSDT"
POLL_INTERVAL = 60  # 秒
STATE_FILE  = "/root/eth_mtf_state.json"  # 状态持久化

# ── Bark 推送（优先） ──────────────────────────────────────
BARK_KEY    = os.environ.get("BARK_KEY", "")
TELEGRAM_BOT = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

# ══════════════════════════════════════════════════════════════
# 数据获取
# ══════════════════════════════════════════════════════════════

def fetch_binance_klines(symbol: str, interval: str = "15m", limit: int = 1500) -> list:
    """从 Binance 获取 K线"""
    import urllib.request
    import json as _json
    
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        raw = _json.loads(resp.read().decode())
        data = []
        for k in raw:
            data.append({
                "ts": int(k[0]) * 1000000,  # 微秒
                "o":  float(k[1]),
                "h":  float(k[2]),
                "l":  float(k[3]),
                "c":  float(k[4]),
                "v":  float(k[5])
            })
        return data
    except Exception as e:
        print(f"[数据] Binance 失败: {e}", flush=True)
        return []

# ══════════════════════════════════════════════════════════════
# 指标函数
# ══════════════════════════════════════════════════════════════

def calc_ema(prices: list, period: int):
    if len(prices) < period: return None
    k = 2 / (period + 1)
    r = sum(prices[:period]) / period
    for p in prices[period:]:
        r = p * k + r * (1 - k)
    return r

def aggregate_h1(m15_data: list) -> list:
    h1 = []
    for j in range(0, len(m15_data), 4):
        chunk = m15_data[j:min(j+4, len(m15_data))]
        if not chunk: continue
        h1.append({
            "ts": chunk[-1]["ts"],
            "o":  chunk[0]["o"],
            "h":  max(x["h"] for x in chunk),
            "l":  min(x["l"] for x in chunk),
            "c":  chunk[-1]["c"],
            "v":  sum(x["v"] for x in chunk)
        })
    return h1

def aggregate_h4(h1_data: list) -> list:
    h4 = []
    for j in range(0, len(h1_data), 4):
        chunk = h1_data[j:min(j+4, len(h1_data))]
        if not chunk: continue
        h4.append({
            "ts": chunk[-1]["ts"],
            "o":  chunk[0]["o"],
            "h":  max(x["h"] for x in chunk),
            "l":  min(x["l"] for x in chunk),
            "c":  chunk[-1]["c"],
            "v":  sum(x["v"] for x in chunk)
        })
    return h4

# ══════════════════════════════════════════════════════════════
# 结构识别
# ══════════════════════════════════════════════════════════════

def detect_swing_structure(h1_data: list) -> dict:
    """1H结构识别：HL/HH/LH/LL"""
    lookback = 10
    if len(h1_data) < lookback * 2:
        return {"type": "CHOP", "last_HH": 0, "last_HL": 0,
                "last_LH": 0, "last_LL": 0,
                "hh_broken": False, "hl_broken": False,
                "ll_broken": False, "lh_broken": False}

    segment = h1_data[-lookback*2:]
    highs = [b["h"] for b in segment]
    lows  = [b["l"] for b in segment]

    swing_highs = []
    swing_lows  = []
    for i in range(1, len(segment) - 1):
        if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
            swing_highs.append({"idx": i, "price": highs[i]})
        if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
            swing_lows.append({"idx": i, "price": lows[i]})

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return {"type": "CHOP", "last_HH": 0, "last_HL": 0,
                "last_LH": 0, "last_LL": 0, "hh_broken": False,
                "hl_broken": False, "ll_broken": False, "lh_broken": False}

    cur = h1_data[-1]["c"]
    sh = swing_highs[-1]["price"]
    sl = swing_lows[-1]["price"]
    ph = swing_highs[-2]["price"] if len(swing_highs) >= 2 else sh
    pl = swing_lows[-2]["price"] if len(swing_lows) >= 2 else sl

    is_uptrend = (sh > ph) and (sl > pl)
    is_dntrend = (sh < ph) and (sl < pl)

    stype = "UPTREND" if is_uptrend else ("DOWNTREND" if is_dntrend else "CHOP")

    return {
        "type": stype,
        "last_HH": sh, "last_HL": sl,
        "last_LH": sh, "last_LL": sl,
        "prev_HL": pl, "prev_LH": ph,
        "hh_broken": cur > sh,
        "hl_broken": cur < sl,
        "ll_broken": cur < sl,
        "lh_broken": cur > sh,
        "cur_price": cur,
    }

# ══════════════════════════════════════════════════════════════
# 信号生成
# ══════════════════════════════════════════════════════════════

def get_4h_regime(h4_data: list) -> str:
    if len(h4_data) < 170: return "NONE"
    closes = [b["c"] for b in h4_data]
    ema144 = calc_ema(closes, 144)
    ema169 = calc_ema(closes, 169)
    if ema144 is None or ema169 is None: return "NONE"
    return "LONG" if ema144 > ema169 else "SHORT"

def generate_signal(m15_all: list) -> dict | None:
    """
    生成MTF信号

    只推送结构破坏/建立时的首次信号：
      - HH BREAKOUT (uptrend + 4H LONG)
      - LL BREAKOUT (downtrend + 4H SHORT)
      - HL/LH 回踩确认

    返回:
      {"type", "direction", "price", "rsi?", "reason", "confidence"}
      或 None
    """
    if len(m15_all) < 1000: return None

    h1_all = aggregate_h1(m15_all)
    h4_all = aggregate_h4(h1_all)

    if len(h4_all) < 170 or len(h1_all) < 50:
        return None

    regime = get_4h_regime(h4_all)
    if regime == "NONE": return None

    structure = detect_swing_structure(h1_all)
    if structure["type"] == "CHOP": return None

    cur = h1_all[-1]["c"]

    # ── 信号判断 ──
    signal = None

    if regime == "LONG" and structure["type"] == "UPTREND":
        # HH 突破做多
        if structure["hh_broken"]:
            signal = {
                "type": "HH_BREAKOUT",
                "direction": "LONG",
                "price": cur,
                "hl": structure["last_HL"],
                "hh": structure["last_HH"],
                "reason": f"HH突破 {structure['last_HH']:.1f} → {cur:.1f}",
                "confidence": "HIGH",
            }
        # HL 回踩确认做多
        elif cur > structure["last_HL"] and cur < structure["prev_LH"] * 0.98:
            signal = {
                "type": "HL_RETEST",
                "direction": "LONG",
                "price": cur,
                "hl": structure["last_HL"],
                "hh": structure["last_HH"],
                "reason": f"HL回踩 {structure['last_HL']:.1f} 不破",
                "confidence": "MEDIUM",
            }

    elif regime == "SHORT" and structure["type"] == "DOWNTREND":
        if structure["ll_broken"]:
            signal = {
                "type": "LL_BREAKOUT",
                "direction": "SHORT",
                "price": cur,
                "ll": structure["last_LL"],
                "lh": structure["last_LH"],
                "reason": f"LL跌破 {structure['last_LL']:.1f} → {cur:.1f}",
                "confidence": "HIGH",
            }
        elif cur < structure["last_LH"] and cur > structure["last_LL"] * 1.02:
            signal = {
                "type": "LH_RETEST",
                "direction": "SHORT",
                "price": cur,
                "ll": structure["last_LL"],
                "lh": structure["last_LH"],
                "reason": f"LH回踩 {structure['last_LH']:.1f} 不破",
                "confidence": "MEDIUM",
            }

    return signal

# ══════════════════════════════════════════════════════════════
# 推送
# ══════════════════════════════════════════════════════════════

def push_bark(title: str, body: str):
    if not BARK_KEY: return
    import urllib.request
    import urllib.parse
    try:
        url = f"https://api.day.app/{BARK_KEY}/{urllib.parse.quote(title)}/{urllib.parse.quote(body)}?isArchive=1&group=ETH-MTF"
        urllib.request.urlopen(url, timeout=10)
        print(f"[推送] Bark: {title}", flush=True)
    except Exception as e:
        print(f"[推送] Bark失败: {e}", flush=True)

def push_telegram(msg: str):
    if not TELEGRAM_BOT or not TELEGRAM_CHAT: return
    import urllib.request
    import json as _json
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT}/sendMessage"
        data = _json.dumps({"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        print(f"[推送] Telegram: ok", flush=True)
    except Exception as e:
        print(f"[推送] Telegram失败: {e}", flush=True)

def push_signal(sig: dict, regime: str):
    """推送信号"""
    direction = sig["direction"]
    emoji = "🟢" if direction == "LONG" else "🔴"
    reg_emoji = "🟢" if regime == "LONG" else "🔴"

    title = f"{emoji} ETH {sig['type']}"
    body_lines = [
        f"信号: {direction} {sig['type']}",
        f"价格: ${sig['price']:.1f}",
        f"置信: {sig['confidence']}",
        f"解释: {sig['reason']}",
        f"",
    ]
    if "hl" in sig and sig["hl"]:
        body_lines.append(f"HL结构: ${sig['hl']:.1f}")
    if "hh" in sig and sig["hh"]:
        body_lines.append(f"HH结构: ${sig['hh']:.1f}")
    if "ll" in sig and sig["ll"]:
        body_lines.append(f"LL结构: ${sig['ll']:.1f}")
    if "lh" in sig and sig["lh"]:
        body_lines.append(f"LH结构: ${sig['lh']:.1f}")
    body_lines.append(f"4H Regime: {reg_emoji} {regime}")

    body = "\n".join(body_lines)
    push_bark(title, body)
    push_telegram(f"<b>{title}</b>\n{body}")

# ══════════════════════════════════════════════════════════════
# 状态追踪
# ══════════════════════════════════════════════════════════════

def load_state() -> dict:
    """恢复上次状态"""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {"last_type": "", "last_hh": 0, "last_hl": 0, "last_ll": 0, "last_lh": 0}

def save_state(state: dict):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def is_new_signal(sig: dict, state: dict) -> bool:
    """判断是否为新信号（避免重复推送）"""
    if sig["type"] != state.get("last_type", ""):
        return True
    # 同类型信号比较价格是否有明显变化
    price_diff = abs(sig["price"] - state.get("last_price", 0))
    return price_diff > state.get("last_price", 0) * 0.005  # 0.5% 变动

# ══════════════════════════════════════════════════════════════
# 主循环
# ══════════════════════════════════════════════════════════════

def run_once() -> bool:
    """执行一次扫描，返回是否推送了信号"""
    m15 = fetch_binance_klines(SYMBOL)
    if not m15 or len(m15) < 1000:
        print(f"[扫描] 数据不足: {len(m15) if m15 else 0}", flush=True)
        return False

    sig = generate_signal(m15)
    if not sig:
        print(f"[扫描] 无信号", flush=True)
        return False

    h1 = aggregate_h1(m15)
    h4 = aggregate_h4(h1)
    regime = get_4h_regime(h4)

    state = load_state()
    if not is_new_signal(sig, state):
        print(f"[扫描] 信号重复，跳过", flush=True)
        return False

    push_signal(sig, regime)
    state["last_type"] = sig["type"]
    state["last_price"] = sig["price"]
    state["last_hh"] = sig.get("hh", 0)
    state["last_hl"] = sig.get("hl", 0)
    state["last_ll"] = sig.get("ll", 0)
    state["last_lh"] = sig.get("lh", 0)
    save_state(state)
    print(f"[扫描] 推送: {sig['type']} @ ${sig['price']:.1f}", flush=True)
    return True

def main():
    print(f"🐱 旺财 MTF Signal Service 启动")
    print(f"   品种: {SYMBOL} | 轮询: {POLL_INTERVAL}s")
    print(f"   推送: {'Bark' if BARK_KEY else '无'}{'+Telegram' if TELEGRAM_BOT else ''}")
    print(f"   状态: {STATE_FILE}")
    print(f"   {'='*40}", flush=True)

    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[错误] {e}", flush=True)
            import traceback
            traceback.print_exc()
        time.sleep(POLL_INTERVAL)

# ══════════════════════════════════════════════════════════════
# 单次运行（用于测试/systemd）
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    else:
        main()
