#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
旺财 v5 五层级联信号系统
=====================================================================
架构升级：从"4H Regime + 1H Structure"单线 → 五层降级级联

信号瀑布：
  Layer 1 [主趋势]: 4H Regime + 1H Structure + 15m Entry 一致
  Layer 2 [结构信号]: 1H 单独有趋势（不要求4H对齐）
  Layer 3 [微型趋势]: 15m 短周期微结构
  Layer 4 [极端超卖]: 15m RSI 超买/超卖
  Layer 5 [静默简报]: 6小时无推送 → 自动市场快报

不变：
  - 所有交易决策逻辑（arbitrate, market quality）
  - Telegram 命令、AI 分析、清算
  - 冷却规则、模式记忆、推送接口
"""
import requests, time, json, os

# ── 配置（与旧版完全兼容） ──────────────────────────────────
TG = "8640664409:AAHPZIZd1YGa6jCwXziM01qoJ0RJwCfG-LM"
TC = "8410098965"
DK = "sk-eevlkxrnmfgtoxyijmftmcexvqrdkjkokmhszpiebjfwhgvm"
DU = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
BK = "gbRTde9uu3C8AwZBqorEj8"
GA = "https://api.gateio.ws/api/v4/futures/usdt"
MF = "/root/wangcai_memory.json"

# ── 状态 ──────────────────────────────────────────────────
L = 0; M = []; X = {}; ST = {"t":0,"w":0,"l":0,"p":0,"s":0}
SL = {}  # 信号冷却
SIG_LAST = ""; SIG_TIME = 0  # 上次推送的信号
LAST_PUSH_TIME = time.time()  # 上次任何推送的时间（用于静默简报）
POS = {"direction": "", "entry": 0, "leverage": 0, "size": 0, "time": 0, "pnl": 0}  # 持仓追踪

# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

def push(t, b=""):
    global LAST_PUSH_TIME
    LAST_PUSH_TIME = time.time()
    for _ in range(2):
        try:
            requests.post(f"https://api.telegram.org/bot{TG}/sendMessage",
                          json={"chat_id": TC, "text": t[:2000]}, timeout=10)
            requests.post("https://api.day.app/push",
                          json={"device_key": BK, "title": t[:50], "body": b[:200],
                                "group": "旺财", "level": "timeSensitive", "badge": 1},
                          timeout=8)
            return
        except:
            time.sleep(0.5)

def kl(iv, lim):
    """多源K线获取：Gate.io → Binance → OKX 自动降级"""
    sources = [
        {"name": "Gate.io", "url": f"{GA}/candlesticks",
         "params": {"contract": "ETH_USDT", "interval": iv, "limit": lim},
         "parse": lambda d: [{"c": float(x["c"]), "h": float(x["h"]), "l": float(x["l"]), "v": float(x["v"])} for x in (d.reverse() or d)]},
        {"name": "Binance", "url": "https://fapi.binance.com/fapi/v1/klines",
         "params": {"symbol": "ETHUSDT", "interval": iv, "limit": lim},
         "parse": lambda d: [{"c": float(x[4]), "h": float(x[2]), "l": float(x[3]), "v": float(x[5])} for x in d]},
        {"name": "OKX", "url": "https://www.okx.com/api/v5/market/candles",
         "params": {"instId": "ETH-USDT-SWAP", "bar": iv.replace("4h","4H").replace("15m","15m"), "limit": str(lim)},
         "parse": lambda d: [{"c": float(x[4]), "h": float(x[2]), "l": float(x[3]), "v": float(x[5])} for x in d.get("data",[])]},
    ]
    for src in sources:
        try:
            r = requests.get(src["url"], params=src["params"], timeout=8)
            if r.status_code != 200: continue
            data = r.json()
            if not data: continue
            bars = src["parse"](data)
            if len(bars) >= lim * 0.5:
                return bars
        except:
            continue
    log("⚠️ K线获取失败: 所有数据源均不可用")
    return []

def ema(d, p):
    if len(d) < p: return None
    k = 2 / (p + 1); r = sum(x["c"] for x in d[:p]) / p
    for x in d[p:]: r = x["c"] * k + r * (1 - k)
    return r

def rsi(d, p=14):
    if len(d) < p + 1: return 50
    g = l = 0
    for i in range(1, p + 1):
        z = d[i]["c"] - d[i-1]["c"]
        if z > 0: g += z
        else: l -= z
    return 100 - 100 / (1 + (g / p) / (l / p)) if l > 0 else 100

def price():
    """多源价格获取：Gate.io → Binance → OKX"""
    sources = [
        ("Gate.io", f"{GA}/tickers", {"contract": "ETH_USDT"},
         lambda d: (float(d[0]["last"]), float(d[0]["volume_24h"]), float(d[0]["low_24h"]), float(d[0]["high_24h"]))),
        ("Binance", "https://fapi.binance.com/fapi/v1/ticker/24hr", {"symbol": "ETHUSDT"},
         lambda d: (float(d["lastPrice"]), float(d["volume"]), float(d["lowPrice"]), float(d["highPrice"]))),
        ("OKX", "https://www.okx.com/api/v5/market/ticker", {"instId": "ETH-USDT-SWAP"},
         lambda d: (float(d["data"][0]["last"]), float(d["data"][0]["volCcy24h"]), float(d["data"][0]["low24h"]), float(d["data"][0]["high24h"]))),
    ]
    for name, url, params, parse in sources:
        try:
            r = requests.get(url, params=params, timeout=8)
            if r.status_code == 200:
                return parse(r.json())
        except: pass
    log("⚠️ 价格获取失败: 所有数据源均不可用")
    return 0, 0, 0, 0

def liq():
    try:
        r = requests.get("https://www.okx.com/api/v5/public/liquidation-orders",
            params={"instType":"SWAP","instFamily":"ETH-USDT","state":"filled","limit":"50"},
            timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200: return {}
        data = r.json().get("data", []); lo = {}; so = {}; tl = 0; ts = 0
        for item in data:
            for d in item.get("details", []):
                px = round(float(d["bkPx"]) / 5) * 5; usd = float(d["sz"]) * px
                if d["posSide"] == "long": lo[px] = lo.get(px, 0) + usd; tl += usd
                else: so[px] = so.get(px, 0) + usd; ts += usd
        return {"tl": round(tl), "ts": round(ts),
                "lc": [{"p":p,"a":round(a)} for p,a in sorted(lo.items(),key=lambda x:-x[1])[:3]],
                "sc": [{"p":p,"a":round(a)} for p,a in sorted(so.items(),key=lambda x:-x[1])[:3]]}
    except: return {}

# ══════════════════════════════════════════════════════════════
# 信号中文映射
# ══════════════════════════════════════════════════════════════
SIGNAL_CN = {
    "STRUCTURE_BROKEN": "结构破坏",
    "HH_BREAKOUT": "HH突破",
    "HL_RETEST": "HL回测",
    "LL_BREAKOUT": "LL突破",
    "LH_RETEST": "LH回测",
    "CHOP": "震荡",
    "EXIT": "退出",
    "LONG": "做多",
    "SHORT": "做空",
    "STRUCTURE_ONLY": "结构信号",
    "MICRO_TREND": "微型趋势",
    "OVERSOLD": "超卖",
    "OVERBOUGHT": "超买",
    "SILENT_REPORT": "市场简报",
}

# ══════════════════════════════════════════════════════════════
# MTF 结构决策引擎（原版，不变）
# ══════════════════════════════════════════════════════════════

def get_regime(h4_data: list) -> str:
    """4H EMA144/169 → Bias"""
    if len(h4_data) < 170: return "NONE"
    closes = [b["c"] for b in h4_data]
    e144 = ema(h4_data, 144)
    e169 = ema(h4_data, 169)
    if e144 is None or e169 is None: return "NONE"
    return "LONG" if e144 > e169 else "SHORT"

def detect_1h_structure(h1_data: list) -> dict:
    """1H Swing HL/HH → 结构识别"""
    lb = 10
    if len(h1_data) < lb * 2:
        return {"type": "CHOP", "last_HH": 0, "last_HL": 0, "hh_broken": False, "hl_broken": False}
    seg = h1_data[-lb*2:]
    highs = [b["h"] for b in seg]
    lows = [b["l"] for b in seg]
    sh, sl = [], []
    for i in range(1, len(seg) - 1):
        if highs[i] > highs[i-1] and highs[i] > highs[i+1]: sh.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i+1]: sl.append(lows[i])
    if len(sh) < 2 or len(sl) < 2:
        return {"type": "CHOP", "last_HH": 0, "last_HL": 0, "hh_broken": False, "hl_broken": False}
    last_hh = sh[-1]; prev_hh = sh[-2]; last_hl = sl[-1]; prev_hl = sl[-2]
    cur = h1_data[-1]["c"]
    uptrend = (last_hh > prev_hh) and (last_hl > prev_hl)
    dntrend = (last_hh < prev_hh) and (last_hl < prev_hl)
    stype = "UPTREND" if uptrend else ("DOWNTREND" if dntrend else "CHOP")
    return {
        "type": stype, "last_HH": last_hh, "last_HL": last_hl,
        "prev_HH": prev_hh, "prev_HL": prev_hl,
        "hh_broken": cur > last_hh, "hl_broken": cur < last_hl,
        "structure_hold": not (cur < last_hl if stype == "UPTREND" else cur > last_hh),
        "cur_price": cur,
    }

def aggregate_to_h1_h4(m15):
    """将15m K线聚合为1H和4H"""
    h1 = []
    for j in range(0, len(m15), 4):
        chunk = m15[j:min(j+4, len(m15))]
        if not chunk: continue
        h1.append({"c": chunk[-1]["c"], "h": max(x["h"] for x in chunk),
                    "l": min(x["l"] for x in chunk)})
    h4 = []
    for j in range(0, len(h1), 4):
        chunk = h1[j:min(j+4, len(h1))]
        if not chunk: continue
        h4.append({"c": chunk[-1]["c"], "h": max(x["h"] for x in chunk),
                    "l": min(x["l"] for x in chunk)})
    return h1, h4


def assess_market_quality(m15: list) -> dict:
    """市场质量评分 (0.0~1.0) — 原版不变"""
    if len(m15) < 30:
        return {"score": 0.0, "state": "CHOP", "details": {}}

    def calc_atr(bars, period=14):
        if len(bars) < period + 1: return 0
        trs = []
        for i in range(1, len(bars)):
            hl = bars[i]["h"] - bars[i]["l"]
            hc = abs(bars[i]["h"] - bars[i-1]["c"])
            lc = abs(bars[i]["l"] - bars[i-1]["c"])
            trs.append(max(hl, hc, lc))
        return sum(trs[-period:]) / period if trs else 0

    current_atr = calc_atr(m15[-20:])
    atr_hist = [calc_atr(m15[j-20:j]) for j in range(20, min(len(m15), 200), 4)]
    atr_hist_sorted = sorted(atr_hist)
    atr_pct = sum(1 for a in atr_hist_sorted if a < current_atr) / max(len(atr_hist_sorted), 1)
    vol_score = 0.0
    if 0.4 <= atr_pct <= 0.8: vol_score = 1.0
    elif 0.3 <= atr_pct < 0.4 or 0.8 < atr_pct <= 0.9: vol_score = 0.6
    elif atr_pct < 0.3 or atr_pct > 0.9: vol_score = 0.3

    closes = [b["c"] for b in m15[-20:]]
    up_moves = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
    down_moves = len(closes) - 1 - up_moves
    direction_ratio = max(up_moves, down_moves) / (len(closes) - 1)
    trend_score = (direction_ratio - 0.5) * 2

    vol_avg = sum(b["v"] for b in m15[-20:]) / 20 if len(m15) >= 20 else 1
    cur_vol = m15[-1]["v"]
    vol_ratio = cur_vol / vol_avg if vol_avg > 0 else 1.0
    if 0.5 <= vol_ratio <= 2.0: vol_score_q = 1.0
    elif vol_ratio > 3.0 or vol_ratio < 0.3: vol_score_q = 0.3
    else: vol_score_q = 0.7

    h1, _ = aggregate_to_h1_h4(m15)
    struct = detect_1h_structure(h1) if len(h1) >= 20 else {"type": "CHOP"}
    clarity_score = 1.0 if struct["type"] != "CHOP" else 0.2

    final_score = (vol_score * 0.30 + trend_score * 0.25 + vol_score_q * 0.20 + clarity_score * 0.25)
    if final_score >= 0.6: state = "HIGH_QUALITY"
    elif final_score >= 0.35: state = "NOISY"
    else: state = "CHOP"

    return {"score": round(final_score, 2), "state": state,
            "details": {"volatility": round(vol_score, 2), "trend_strength": round(trend_score, 2),
                        "volume_quality": round(vol_score_q, 2), "structure_clarity": round(clarity_score, 2),
                        "atr_percentile": round(atr_pct, 2), "vol_ratio": round(vol_ratio, 2)}}


# ══════════════════════════════════════════════════════════════
# Pattern Error Memory — 结构自我校准
# ══════════════════════════════════════════════════════════════

PATTERN_MEMORY = {}

def record_pattern_result(pattern: str, success: bool):
    if pattern not in PATTERN_MEMORY:
        PATTERN_MEMORY[pattern] = {"t": 0, "w": 0, "l": 0}
    PATTERN_MEMORY[pattern]["t"] += 1
    if success: PATTERN_MEMORY[pattern]["w"] += 1
    else: PATTERN_MEMORY[pattern]["l"] += 1
    total = PATTERN_MEMORY[pattern]["t"]
    PATTERN_MEMORY[pattern]["rate"] = round(PATTERN_MEMORY[pattern]["w"] / total, 2) if total else 0

def get_pattern_confidence(pattern: str) -> float:
    if pattern not in PATTERN_MEMORY or PATTERN_MEMORY[pattern]["t"] < 3:
        return 0.5
    return PATTERN_MEMORY[pattern]["rate"]


# ══════════════════════════════════════════════════════════════
# ⭐ 新增：15m 微型结构检测
# ══════════════════════════════════════════════════════════════

def detect_15m_micro_structure(m15: list) -> dict:
    """
    15m 级别微结构检测（短周期 swing 识别）
    用于 Layer 3 降级信号，在1H震荡时捕捉15m短线趋势。

    Returns:
        {"type": "UPTREND"/"DOWNTREND"/"CHOP",
         "swing_high": float, "swing_low": float,
         "strength": 1.0/0.5/0.0}
    """
    if len(m15) < 20:
        return {"type": "CHOP", "swing_high": 0, "swing_low": 0, "strength": 0.0}

    seg = m15[-24:]  # 6小时数据（24根15m）
    highs = [b["h"] for b in seg]
    lows = [b["l"] for b in seg]

    # 找局部 swing high/low (5根K线判断)
    sh, sl = [], []
    for i in range(2, len(seg) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            sh.append((i, highs[i]))
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            sl.append((i, lows[i]))
    if len(sh) < 2 or len(sl) < 2:
        # 连2个swing都找不到 -> 完全震荡
        return {"type": "CHOP", "swing_high": highs[-1], "swing_low": lows[-1], "strength": 0.0}

    last_sh = sh[-1][1]; prev_sh = sh[-2][1]
    last_sl = sl[-1][1]; prev_sl = sl[-2][1]
    cur = seg[-1]["c"]

    micro_up = (last_sh > prev_sh) and (last_sl > prev_sl)
    micro_dn = (last_sh < prev_sh) and (last_sl < prev_sl)

    if micro_up:
        strength = 1.0 if (last_sh - prev_sh) / prev_sh > 0.003 else 0.5  # 0.3%涨幅=强
        return {"type": "UPTREND", "swing_high": last_sh, "swing_low": last_sl, "strength": strength,
                "reason": f"15m微升 {prev_sl:.0f}→{last_sl:.0f}"}
    if micro_dn:
        strength = 1.0 if (prev_sl - last_sl) / prev_sl > 0.003 else 0.5
        return {"type": "DOWNTREND", "swing_high": last_sh, "swing_low": last_sl, "strength": strength,
                "reason": f"15m微降 {prev_sh:.0f}→{last_sh:.0f}"}

    return {"type": "CHOP", "swing_high": highs[-1], "swing_low": lows[-1], "strength": 0.0}


# ══════════════════════════════════════════════════════════════
# ⭐ 新增：五层级联信号生成
# ══════════════════════════════════════════════════════════════

def arbitrate(m15: list, h1_data: list = None) -> dict:
    """Decision Arbiter（原版不变）"""
    if len(m15) < 100:
        return {"score": 0, "direction": "CHOP", "details": {}, "reason": "数据不足"}
    h1 = h1_data
    if h1 is None:
        h1, _ = aggregate_to_h1_h4(m15)
    _, h4 = aggregate_to_h1_h4(m15)
    cur = m15[-1]["c"]
    details = {}
    score = 0

    regime = get_regime(h4)
    regime_score = 0
    if regime == "LONG": regime_score = +2
    elif regime == "SHORT": regime_score = -2
    score += regime_score
    details["4H_Regime"] = {"value": regime, "score": regime_score}

    struct = detect_1h_structure(h1)
    struct_score = 0; struct_note = struct["type"]
    if struct["type"] == "UPTREND":
        if struct["structure_hold"]:
            struct_score = +2
            struct_note = f"上升结构保持(HL={struct['last_HL']:.0f})"
        else:
            struct_score = 0
            struct_note = f"结构破坏(HL跌破={struct['last_HL']:.0f})"
    elif struct["type"] == "DOWNTREND":
        if struct["structure_hold"]:
            struct_score = -2
            struct_note = f"下降结构保持(LH={struct['last_HH']:.0f})"
        else:
            struct_score = 0
            struct_note = f"结构破坏(LH突破={struct['last_HH']:.0f})"
    score += struct_score
    details["1H_Structure"] = {"value": struct_note, "score": struct_score}

    entry_score = 0; entry_note = "无"
    if regime == "LONG" and struct["type"] == "UPTREND" and struct["structure_hold"]:
        if struct["hh_broken"]:
            entry_score = +1; entry_note = f"HH突破({struct['last_HH']:.0f})"
        elif cur > struct["last_HL"] and cur < struct["last_HH"] * 0.98:
            entry_score = +0.5; entry_note = f"HL回测({struct['last_HL']:.0f})"
    elif regime == "SHORT" and struct["type"] == "DOWNTREND" and struct["structure_hold"]:
        if cur < struct["last_HL"]:
            entry_score = -1; entry_note = f"LL突破({struct['last_HL']:.0f})"
        elif cur > struct["last_HL"] * 1.02 and cur < struct["last_HH"]:
            entry_score = -0.5; entry_note = f"LH回测({struct['last_HH']:.0f})"
    score += entry_score
    details["15m_Entry"] = {"value": entry_note, "score": entry_score}

    quality = assess_market_quality(m15)
    quality_score = quality["score"]
    quality_state = quality["state"]
    details["MarketQuality"] = {"value": quality_state, "score": quality_score, "details": quality["details"]}

    if quality_score < 0.35:
        return {"score": score, "direction": "CHOP", "type": "CHOP", "price": cur,
                "details": details, "reason": f"低质量行情({quality_state} {quality_score:.2f})，不交易",
                "market_quality": quality, "confidence": 0.0}

    raw_score = score
    quality_mult = 1.0 if quality_state == "HIGH_QUALITY" else 0.7
    adjusted_score = raw_score * quality_mult

    if adjusted_score >= +3:
        direction = "LONG"; signal_type = "HH_BREAKOUT" if entry_note.startswith("HH") else "HL_RETEST"
    elif adjusted_score <= -3:
        direction = "SHORT"; signal_type = "LL_BREAKOUT" if entry_note.startswith("LL") else "LH_RETEST"
    else:
        direction = "CHOP"; signal_type = "CHOP"

    if not struct["structure_hold"] and struct["type"] != "CHOP":
        direction = "EXIT"; signal_type = "STRUCTURE_BROKEN"

    pat_conf = get_pattern_confidence(signal_type) if direction != "CHOP" else 0.0
    if direction != "CHOP" and pat_conf < 0.3 and PATTERN_MEMORY.get(signal_type, {}).get("t", 0) >= 3:
        direction = "CHOP"; signal_type = "CHOP"

    base_conf = min(abs(adjusted_score) / 6.0, 1.0)
    final_conf = round(base_conf * quality_score * (pat_conf + 0.5), 2)

    return {"score": raw_score, "adjusted_score": round(adjusted_score, 1),
            "direction": direction, "type": signal_type, "price": cur, "details": details,
            "reason": f"总评{raw_score:+d}(调后{adjusted_score:+.0f}): "
                      f"{details['4H_Regime']['value']}({details['4H_Regime']['score']:+d}) + "
                      f"{details['1H_Structure']['value']}({details['1H_Structure']['score']:+d})",
            "market_quality": quality,
            "pattern_memory": {"pattern": signal_type, "confidence": pat_conf} if direction != "CHOP" else {},
            "confidence": final_conf}


# ══════════════════════════════════════════════════════════════
# ⭐ 五层信号级联主入口
# ══════════════════════════════════════════════════════════════

SIGNAL_LAST_TIMES = {}  # 各层级的冷却追踪

def cascade_signal(m15: list) -> dict | None:
    """
    级联信号生成：从高到低逐个降层

    Returns:
        dict with keys: type, direction, price, reason, layer(1-5), confidence
        None = 无任何信号（极端罕见）
    """
    global PATTERN_MEMORY
    if len(m15) < 100: return None

    h1, h4 = aggregate_to_h1_h4(m15)
    cur = m15[-1]["c"]
    cur_rsi = rsi(m15[-20:], 14) if len(m15) >= 21 else 50

    # ── Layer 1: MTF 主趋势（原版精确交易信号） ──
    sig = arbitrate(m15, h1)
    if sig and sig["direction"] != "CHOP":
        type_cn = SIGNAL_CN.get(sig["type"], sig["type"])
        if _cooldown_ok("L1_" + sig["type"], 900):  # 15min冷却
            _mark_cooldown("L1_" + sig["type"])
            return {"type": sig["type"], "direction": sig["direction"], "price": cur,
                    "reason": sig["reason"], "confidence": sig["confidence"],
                    "layer": 1, "label": "主趋势", "data": sig}

    # ── Layer 2: 1H 结构单独信号 ──
    struct = detect_1h_structure(h1)
    if struct["type"] in ("UPTREND", "DOWNTREND") and struct["structure_hold"]:
        dir_str = "LONG" if struct["type"] == "UPTREND" else "SHORT"
        if _cooldown_ok("L2_STRUCTURE", 3600):  # 1小时冷却
            _mark_cooldown("L2_STRUCTURE")
            reason = (f"1H{dir_str}结构保持 HH={struct['last_HH']:.0f} HL={struct['last_HL']:.0f}"
                      f"\n4H方向不明确，但1H趋势清晰，建议关注回调入场")
            return {"type": "STRUCTURE_ONLY", "direction": dir_str, "price": cur,
                    "reason": reason, "confidence": 0.4, "layer": 2, "label": "结构信号"}

    # ── Layer 3: 15m 微结构（短线趋势） ──
    micro = detect_15m_micro_structure(m15)
    if micro["type"] in ("UPTREND", "DOWNTREND") and micro["strength"] >= 0.5:
        dir_str = "LONG" if micro["type"] == "UPTREND" else "SHORT"
        if _cooldown_ok("L3_MICRO", 1800):  # 30分钟冷却
            _mark_cooldown("L3_MICRO")
            reason = micro["reason"]
            return {"type": "MICRO_TREND", "direction": dir_str, "price": cur,
                    "reason": reason, "confidence": 0.3, "layer": 3, "label": "微型趋势"}

    # ── Layer 4: RSI 极端监测 ──
    if cur_rsi <= 25:
        if _cooldown_ok("L4_OVERSOLD", 7200):  # 2小时冷却
            _mark_cooldown("L4_OVERSOLD")
            return {"type": "OVERSOLD", "direction": "WATCH_LONG", "price": cur,
                    "reason": f"15m RSI {cur_rsi:.0f} 超卖区，可能的短线反弹机会",
                    "confidence": 0.25, "layer": 4, "label": "超卖"}
    elif cur_rsi >= 75:
        if _cooldown_ok("L4_OVERBOUGHT", 7200):
            _mark_cooldown("L4_OVERBOUGHT")
            return {"type": "OVERBOUGHT", "direction": "WATCH_SHORT", "price": cur,
                    "reason": f"15m RSI {cur_rsi:.0f} 超买区，可能的短线回调机会",
                    "confidence": 0.25, "layer": 4, "label": "超买"}

    return None  # 全部降层完毕，无信号


def _cooldown_ok(key: str, seconds: int) -> bool:
    """检查冷却是否过期"""
    return (key not in SIGNAL_LAST_TIMES or
            time.time() - SIGNAL_LAST_TIMES[key] > seconds)

def _mark_cooldown(key: str):
    """设置冷却时间"""
    SIGNAL_LAST_TIMES[key] = time.time()


# ══════════════════════════════════════════════════════════════
# ⭐ 新增：静默简报（Layer 5）
# ══════════════════════════════════════════════════════════════

def build_silent_report(m15: list) -> dict | None:
    """
    6小时无推送 → 生成市场快报
    确保永不静默
    """
    h1, h4 = aggregate_to_h1_h4(m15)
    cur = m15[-1]["c"]
    cur_rsi = rsi(m15[-20:], 14) if len(m15) >= 21 else 50
    regime = get_regime(h4)
    struct = detect_1h_structure(h1)
    quality = assess_market_quality(m15)

    struct_cn = {"UPTREND": "📈上升", "DOWNTREND": "📉下降", "CHOP": "〰️震荡"}.get(struct["type"], "?")
    regime_cn = {"LONG": "多头", "SHORT": "空头", "NONE": "不明"}.get(regime, "?")
    rsi_label = "超卖" if cur_rsi <= 30 else ("超买" if cur_rsi >= 70 else "中性")
    quality_cn = {"HIGH_QUALITY": "优质", "NOISY": "一般", "CHOP": "差"}.get(quality["state"], "?")

    reason = (f"${cur:.0f}\n"
              f"4H:{regime_cn} | 1H:{struct_cn}\n"
              f"RSI:{cur_rsi:.0f}({rsi_label}) | 质量:{quality_cn}\n"
              f"HH={struct['last_HH']:.0f} HL={struct['last_HL']:.0f}")

    if struct["type"] == "UPTREND" and struct["structure_hold"]:
        reason += "\n⬆️ 多头结构保持，不做空"
    elif struct["type"] == "DOWNTREND" and struct["structure_hold"]:
        reason += "\n⬇️ 空头结构保持，不做多"
    else:
        reason += "\n➖ 结构未明，等待趋势确认"

    return {"type": "SILENT_REPORT", "direction": "INFO", "price": cur,
            "reason": reason, "confidence": 1.0, "layer": 5, "label": "市场简报"}


# ══════════════════════════════════════════════════════════════
# 推送函数（增强：层级标签）
# ══════════════════════════════════════════════════════════════

def push_signal(sig: dict):
    """格式化推送级联信号（含持仓冲突检测）"""
    emoji_map = {"LONG": "🟢", "SHORT": "🔴", "EXIT": "⚪", "WATCH_LONG": "🟡", "WATCH_SHORT": "🟡", "INFO": "ℹ️"}
    layer_label = {"1": "主趋势", "2": "结构信号", "3": "微型趋势", "4": "超买/超卖", "5": "市场简报"}
    emoji = emoji_map.get(sig["direction"], "⚪")
    layer = sig.get("layer", 0)
    label = layer_label.get(str(layer), "")

    type_cn = SIGNAL_CN.get(sig["type"], sig["type"])
    title = f"{emoji}{type_cn}"
    if layer <= 4:
        title += f" L{layer}"

    # 持仓冲突检测
    conflict_warn = ""
    if POS["direction"] and sig["direction"] in ("LONG", "SHORT"):
        if POS["direction"] == "LONG" and sig["direction"] == "SHORT":
            conflict_warn = "\n\n⚠️ 你持多单，当前信号偏空！"
        elif POS["direction"] == "SHORT" and sig["direction"] == "LONG":
            conflict_warn = "\n\n⚠️ 你持空单，当前信号偏多！"

    body = (f"{label}\n"
            f"{sig['reason']}{conflict_warn}")

    push(title, body)
    conf = sig.get("confidence", 0)
    log(f"PUSH L{layer}:{type_cn} {sig['direction']} conf={conf:.0%} @${sig['price']:.0f}{' ⚔️冲突' if conflict_warn else ''}")


# ══════════════════════════════════════════════════════════════
# AI 分析（保留旧版）
# ══════════════════════════════════════════════════════════════

def think(prompt, t=20):
    msgs = [{"role": "system",
             "content": "你是ETH狙击手旺财。基于以下数据给出交易判断。格式：结论+证据+风险+动作。禁止矛盾句。"},
            {"role": "user", "content": prompt}]
    for a in range(2):
        try:
            r = requests.post(DU, json={"model": "deepseek-v4-pro", "messages": msgs, "max_tokens": 300},
                              headers={"Authorization": f"Bearer {DK}"}, timeout=t)
            if r.status_code != 200: continue
            msg = r.json()["choices"][0]["message"]
            if msg.get("tool_calls"):
                msgs.append(msg)
                for tc in msg["tool_calls"]:
                    fn = tc["function"]["name"]
                    if fn == "price": p, _, _, _ = price(); result = {"p": p}
                    elif fn == "liq": result = liq()
                    else: result = {}
                    msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result)})
                r2 = requests.post(DU, json={"model": "deepseek-v4-pro", "messages": msgs, "max_tokens": 300},
                                   headers={"Authorization": f"Bearer {DK}"}, timeout=t)
                if r2.status_code == 200: return r2.json()["choices"][0]["message"].get("content", "")
            else: return msg.get("content", "")
        except Exception as e:
            log(f"AI:{e}")
            if a < 1: time.sleep(1)
    return "AI不通"


# ══════════════════════════════════════════════════════════════
# Telegram 命令（原版不变）
# ══════════════════════════════════════════════════════════════

def handle():
    global L
    try:
        r = requests.get(f"https://api.telegram.org/bot{TG}/getUpdates",
                         params={"offset": L+1, "timeout": 10}, timeout=15)
        if r.status_code != 200: return
        for upd in r.json().get("result", []):
            L = max(L, upd["update_id"])
            msg = upd.get("message", {}); cid = msg.get("chat", {}).get("id")
            txt = msg.get("text", "").strip()
            if not txt or str(cid) != TC: continue
            log(f"cmd:{txt[:30]}")
            if txt == "/start":
                push("旺财MTF v5级联\n/eth /liq /signal /arbiter /stats /regime /pos\n直接聊=AI分析", "")
            elif txt == "/eth":
                p, v, lo, hi = price()
                push(f"ETH ${p:.0f} 24h${hi:.0f}~${lo:.0f}", "") if p else push("失败", "")
            elif txt == "/liq":
                d = liq()
                if not d: push("无清算", "")
                else:
                    rr = max(d["tl"], d["ts"]) / max(min(d["tl"], d["ts"]), 1)
                    m = f"清算 多${d['tl']/1e6:.0f}M/空${d['ts']/1e6:.0f}M 比1:{rr:.0f}"
                    if d.get("sc"): m += f"\n空密${d['sc'][0]['p']}(${d['sc'][0]['a']/1e4:.0f}万)"
                    if d.get("lc"): m += f"\n多密${d['lc'][0]['p']}(${d['lc'][0]['a']/1e4:.0f}万)"
                    push(m, ""); X["p"] = price()[0]
            elif txt == "/signal":
                m15 = kl("15m", 200)
                if m15:
                    sig = cascade_signal(m15)
                    if sig:
                        push(f"级联 L{sig['layer']}: {sig['label']}\n{dict(sig).get('reason','')}", "")
                    else:
                        push("各层级均无信号", "")
            elif txt == "/arbiter":
                m15 = kl("15m", 200)
                if m15:
                    arb = arbitrate(m15)
                    mq = arb.get("market_quality", {})
                    mqd = mq.get("details", {})
                    msg = (f"Arbiter 总分{arb['score']:+d} → {arb['direction']}\n"
                           f"市场质量: {mq.get('state','?')} ({mq.get('score',0):.2f})\n"
                           f"  波动:{mqd.get('volatility',0):.2f} 趋势:{mqd.get('trend_strength',0):.2f}\n"
                           f"  量能:{mqd.get('volume_quality',0):.2f} 结构:{mqd.get('structure_clarity',0):.2f}\n"
                           f"{arb['reason']}")
                    push(msg, "")
            elif txt.startswith("/pos"):
                parts = txt.split()
                if len(parts) >= 2:
                    POS["direction"] = parts[1].upper()
                    POS["entry"] = float(parts[2]) if len(parts) > 2 else 0
                    POS["leverage"] = float(parts[3].replace("x","")) if len(parts) > 3 else 0
                    POS["size"] = float(parts[4]) if len(parts) > 4 else 0
                    POS["time"] = time.time()
                    push(f"持仓已记录: {POS['direction']} {POS['leverage']}x ${POS['entry']:.0f}", "")
                elif txt == "/pos":
                    if POS["direction"]:
                        p = price()[0]
                        if POS["direction"] == "LONG": pl = (p - POS["entry"]) / POS["entry"] * POS["leverage"] * 100 if POS["entry"] else 0
                        else: pl = (POS["entry"] - p) / POS["entry"] * POS["leverage"] * 100 if POS["entry"] else 0
                        push(f"持仓: {POS['direction']} {POS['leverage']}x\n开仓: ${POS['entry']:.0f} 当前: ${p:.0f}\n浮盈: {pl:+.1f}%", "")
                    else:
                        push("未记录持仓。用法: /pos SHORT 100x 2310 84USDT", "")
                elif txt == "/pos clear":
                    POS = {"direction": "", "entry": 0, "leverage": 0, "size": 0, "time": 0, "pnl": 0}
                    push("持仓已清除", "")
            elif txt == "/stats":
                push(f"旺财 总{ST['t']} 胜{ST['w']} 负{ST['l']} 率{ST['p']}%/{ST['s']}连胜", "")
            elif txt == "/regime":
                h4 = kl("4h", 200)
                rg = "正在获取..."
                if len(h4) >= 170:
                    e144 = ema(h4, 144); e169 = ema(h4, 169)
                    rg = "LONG" if e144 and e169 and e144 > e169 else "SHORT"
                push(f"4H Regime: {rg}", "")
            elif txt in ("/save", "记录复盘"):
                with open("/root/trading_log.md", "a", encoding="utf-8") as f:
                    f.write(f"\n## {time.strftime('%m-%d %H:%M')} ${X.get('p',0):.0f}\n{X.get('a','')}\n")
                push("复盘已存档", "")
            else:
                p, _, _, _ = price()
                r = think(f"用户问：{txt}\nETH${p:.0f}")
                push(r, ""); X["p"] = p; X["a"] = r
    except Exception as e:
        log(f"tg:{e}")


def verify():
    """验证未结算交易（2小时后）"""
    global ST
    for x in M:
        if x.get("d") and not x.get("r") and time.time() - x["t"] > 7200:
            p = price()[0]
            if x["d"] == "LONG": x["r"] = "W" if p > x["p"] else "L"
            elif x["d"] == "SHORT": x["r"] = "W" if p < x["p"] else "L"
            if x.get("r"):
                ST[x["r"]] = ST.get(x["r"], 0) + 1; ST["t"] += 1
    ST["p"] = round(ST["w"] / max(ST["t"], 1) * 100, 1)
    ST["s"] = 0
    for x in reversed(M):
        if x.get("r") == "W": ST["s"] += 1
        elif x.get("r") == "L": break
    with open(MF, "w") as f: json.dump({"M": M, "ST": ST, "SL": SL}, f, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════
# 主循环 v5
# ══════════════════════════════════════════════════════════════

def main():
    global M, ST, SL, PATTERN_MEMORY, LAST_PUSH_TIME
    try:
        with open(MF) as f:
            d = json.load(f)
            M = d.get("M", []); ST = d.get("ST", ST); SL = d.get("SL", {})
            PATTERN_MEMORY = d.get("PM", {})
    except:
        M = []
    for x in M:
        if x.get("r") == "W": ST["w"] += 1; ST["t"] += 1
        elif x.get("r") == "L": ST["l"] += 1; ST["t"] += 1
    ST["p"] = round(ST["w"] / max(ST["t"], 1) * 100, 1)
    LAST_PUSH_TIME = time.time()  # 初始化
    log(f"旺财MTF v5上线 历史{ST['t']}单 胜率{ST['p']}%")
    log(f"架构: 五层级联+多源+持仓追踪")
    if not os.path.exists("/root/trading_log.md"):
        with open("/root/trading_log.md", "w") as f: f.write("# 复盘\n")

    last_poll = 0
    last_silent_check = time.time()
    SILENT_INTERVAL = 6 * 3600  # 6小时静默触发简报

    while True:
        try: pass
        except: pass

        t = time.time()
        if t - last_poll >= 60:
            last_poll = t
            try:
                verify()
                m15 = kl("15m", 200)
                if not m15 or len(m15) < 100: continue

                # ── 级联信号扫描 ──
                sig = cascade_signal(m15)
                if sig:
                    push_signal(sig)
                    # 记录
                    d = sig["direction"]
                    M.append({"t": t, "ts": time.strftime('%H:%M'), "p": sig['price'],
                              "s": sig['type'], "a": sig['reason'][:60],
                              "d": d, "rg": f"L{sig['layer']}", "conf": sig.get("confidence", 0)})
                    if len(M) > 20: M = M[-20:]
                    with open(MF, "w") as f: json.dump(
                        {"M": M, "ST": ST, "SL": SL, "PM": PATTERN_MEMORY}, f, ensure_ascii=False)

                # ── Layer 5: 静默简报检查 ──
                if t - LAST_PUSH_TIME > SILENT_INTERVAL:
                    if t - last_silent_check > 3600:  # 防止高频重推
                        last_silent_check = t
                        report = build_silent_report(m15)
                        if report:
                            push_signal(report)
                            log(f"PUSH L5:静默简报 @${m15[-1]['c']:.0f}")

            except Exception as e:
                log(f"cycle:{e}")
        time.sleep(1)


if __name__ == "__main__":
    main()
