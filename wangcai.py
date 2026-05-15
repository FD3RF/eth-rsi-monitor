#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
旺财 — MTF Structure Trend Following + AI分析
===============================================
架构：
  4H Regime (EMA144/169) → 方向
  1H Structure (Swing HL/HH) → 结构保持/破坏
  15m Entry (突破/回踩) → 进场信号

保留旧版全部功能：Telegram命令、AI分析、清算、推送
替换决策引擎：从状态机/RSI/模式 → MTF纯结构驱动

名称: MTF Structural Trend Following with Pyramiding
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

# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

def push(t, b=""):
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
    try:
        r = requests.get(f"{GA}/candlesticks",
                         params={"contract": "ETH_USDT", "interval": iv, "limit": lim},
                         timeout=8)
        if r.status_code != 200: return []
        d = r.json(); d.reverse()
        return [{"c": float(x["c"]), "h": float(x["h"]), "l": float(x["l"]), "v": float(x["v"])} for x in d]
    except:
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
    try:
        r = requests.get(f"{GA}/tickers?contract=ETH_USDT", timeout=8)
        if r.status_code == 200:
            d = r.json()[0]
            return float(d["last"]), float(d["volume_24h"]), float(d["low_24h"]), float(d["high_24h"])
    except: pass
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
# MTF 信号中文映射
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
}

# MTF 结构决策引擎
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

def generate_mtf_signal(m15: list) -> dict | None:
    """
    MTF 信号生成
    返回: {"type", "direction", "price", "reason", "regime", "structure"}
    规则: 只有在4H+1H方向一致时才出信号
    """
    if len(m15) < 100: return None
    # 聚合
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

    regime = get_regime(h4)
    if regime == "NONE": return None
    struct = detect_1h_structure(h1)
    if struct["type"] == "CHOP": return None

    cur = m15[-1]["c"]

    # ── LONG 信号 ──
    if regime == "LONG" and struct["type"] == "UPTREND":
        if not struct["structure_hold"]:
            return {"type": "STRUCTURE_BROKEN", "direction": "EXIT", "price": cur,
                    "reason": f"HL{struct['last_HL']:.1f}跌破，结构破坏", "regime": regime,
                    "structure": struct["type"]}
        if struct["hh_broken"]:
            return {"type": "HH_BREAKOUT", "direction": "LONG", "price": cur,
                    "reason": f"HH{struct['last_HH']:.1f}突破 → 继续持多", "regime": regime,
                    "structure": struct["type"], "confidence": "HIGH"}
        if cur > struct["last_HL"] and cur < struct["last_HH"] * 0.98:
            return {"type": "HL_RETEST", "direction": "LONG", "price": cur,
                    "reason": f"HL{struct['last_HL']:.1f}回踩不破", "regime": regime,
                    "structure": struct["type"], "confidence": "MEDIUM"}

    # ── SHORT 信号 ──
    if regime == "SHORT" and struct["type"] == "DOWNTREND":
        if not struct["structure_hold"]:
            return {"type": "STRUCTURE_BROKEN", "direction": "EXIT", "price": cur,
                    "reason": f"LH{struct['last_HH']:.1f}突破，结构破坏", "regime": regime,
                    "structure": struct["type"]}
        if cur < struct["last_HL"]:
            return {"type": "LL_BREAKOUT", "direction": "SHORT", "price": cur,
                    "reason": f"LL{struct['last_HL']:.1f}跌破 → 继续持空", "regime": regime,
                    "structure": struct["type"], "confidence": "HIGH"}
        if cur > struct["last_HL"] * 1.02 and cur < struct["last_HH"]:
            return {"type": "LH_RETEST", "direction": "SHORT", "price": cur,
                    "reason": f"LH{struct['last_HH']:.1f}回踩不破", "regime": regime,
                    "structure": struct["type"], "confidence": "MEDIUM"}

    return None

# ══════════════════════════════════════════════════════════════
# Market Quality Layer — 市场质量评估
# ══════════════════════════════════════════════════════════════

def assess_market_quality(m15: list) -> dict:
    """
    市场质量评分 (0.0~1.0)

    评估维度：
      - 波动状态 (ATR percentil): 低ATR=垃圾行情
      - 趋势强度 (ADX-like approximation): 无趋势=噪声
      - 成交量质量: 放量=高质量, 缩量=低质量
      - 结构清晰度: Swing点间距=结构是否清晰

    返回: {"score": 0.0~1.0, "state": "HIGH_QUALITY"/"NOISY"/"CHOP", "details": {...}}
    """
    if len(m15) < 30:
        return {"score": 0.0, "state": "CHOP", "details": {}}

    # ── 1. 波动状态 (ATR百分位) ──
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
    # 取近60根ATR做百分位
    atr_hist = [calc_atr(m15[j-20:j]) for j in range(20, min(len(m15), 200), 4)]
    atr_hist_sorted = sorted(atr_hist)
    atr_pct = sum(1 for a in atr_hist_sorted if a < current_atr) / max(len(atr_hist_sorted), 1)
    atr_quality = atr_pct  # 0.0=极低波动(差), 1.0=极高波动(未必好)
    # 低ATR降权, 高ATR也降权(极端波动不可预测)
    # 黄金区: 0.4~0.8 百分位
    vol_score = 0.0
    if 0.4 <= atr_pct <= 0.8:
        vol_score = 1.0
    elif 0.3 <= atr_pct < 0.4 or 0.8 < atr_pct <= 0.9:
        vol_score = 0.6
    elif atr_pct < 0.3 or atr_pct > 0.9:
        vol_score = 0.3

    # ── 2. 趋势强度 (价格方向性) ──
    # 用近20根close计算方向性比率
    closes = [b["c"] for b in m15[-20:]]
    up_moves = sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1])
    down_moves = len(closes) - 1 - up_moves
    direction_ratio = max(up_moves, down_moves) / (len(closes) - 1)  # 0.5~1.0
    # 0.5=完全无方向, 1.0=完全单边
    trend_score = (direction_ratio - 0.5) * 2  # 0.0~1.0

    # ── 3. 成交量质量 ──
    vol_avg = sum(b["v"] for b in m15[-20:]) / 20 if len(m15) >= 20 else 1
    cur_vol = m15[-1]["v"]
    vol_ratio = cur_vol / vol_avg if vol_avg > 0 else 1.0
    # 0.5~2.0 倍量为正常区
    if 0.5 <= vol_ratio <= 2.0:
        vol_score_q = 1.0
    elif vol_ratio > 3.0 or vol_ratio < 0.3:
        vol_score_q = 0.3  # 异常量=不可信
    else:
        vol_score_q = 0.7

    # ── 4. 结构清晰度 ──
    h1 = []
    for j in range(0, len(m15), 4):
        chunk = m15[j:min(j+4, len(m15))]
        if not chunk: continue
        h1.append({"c": chunk[-1]["c"], "h": max(x["h"] for x in chunk),
                    "l": min(x["l"] for x in chunk)})
    struct = detect_1h_structure(h1) if len(h1) >= 20 else {"type": "CHOP"}
    clarity_score = 1.0 if struct["type"] != "CHOP" else 0.2

    # ── 综合评分 ──
    final_score = (vol_score * 0.30 + trend_score * 0.25 +
                   vol_score_q * 0.20 + clarity_score * 0.25)

    if final_score >= 0.6:
        state = "HIGH_QUALITY"
    elif final_score >= 0.35:
        state = "NOISY"
    else:
        state = "CHOP"

    return {
        "score": round(final_score, 2),
        "state": state,
        "details": {
            "volatility": round(vol_score, 2),
            "trend_strength": round(trend_score, 2),
            "volume_quality": round(vol_score_q, 2),
            "structure_clarity": round(clarity_score, 2),
            "atr_percentile": round(atr_pct, 2),
            "vol_ratio": round(vol_ratio, 2),
        }
    }


# ══════════════════════════════════════════════════════════════
# Pattern Error Memory — 结构自我校准
# ══════════════════════════════════════════════════════════════

PATTERN_MEMORY = {}  # {"HH_BREAKOUT": {"t":0,"w":0,"l":0,"rate":0.0}, ...}

def record_pattern_result(pattern: str, success: bool):
    """记录结构模式的成败"""
    if pattern not in PATTERN_MEMORY:
        PATTERN_MEMORY[pattern] = {"t": 0, "w": 0, "l": 0}
    PATTERN_MEMORY[pattern]["t"] += 1
    if success:
        PATTERN_MEMORY[pattern]["w"] += 1
    else:
        PATTERN_MEMORY[pattern]["l"] += 1
    total = PATTERN_MEMORY[pattern]["t"]
    PATTERN_MEMORY[pattern]["rate"] = round(PATTERN_MEMORY[pattern]["w"] / total, 2) if total else 0

def get_pattern_confidence(pattern: str) -> float:
    """获取模式的历史置信度（0.0~1.0），无记录返回0.5"""
    if pattern not in PATTERN_MEMORY or PATTERN_MEMORY[pattern]["t"] < 3:
        return 0.5  # 数据不足, 默认中性
    return PATTERN_MEMORY[pattern]["rate"]

def arbitrate(m15: list, h1_data: list = None) -> dict:
    """
    Decision Arbiter：所有传感器打分，统一裁决

    评分规则：
      4H Regime:  LONG=+2  SHORT=-2  NONE=0
      1H Structure: UPTREND+hold=+2  DOWNTREND+hold=-2  CHOP=0  BROKEN=0
      15m Entry:  HH_break=+1  HL_retest=+0.5  LL_break=-1  LH_retest=-0.5

    阈值:
      >= +3 → LONG
      <= -3 → SHORT
      之间  → CHOP（低置信，不交易）
    """
    if len(m15) < 100:
        return {"score": 0, "direction": "CHOP", "details": {}, "reason": "数据不足"}

    # 聚合
    h1 = h1_data
    if h1 is None:
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

    cur = m15[-1]["c"]
    details = {}
    score = 0

    # ── 1. 4H Regime 评分 ──
    regime = get_regime(h4)
    regime_score = 0
    if regime == "LONG": regime_score = +2
    elif regime == "SHORT": regime_score = -2
    score += regime_score
    details["4H_Regime"] = {"value": regime, "score": regime_score}

    # ── 2. 1H Structure 评分 ──
    struct = detect_1h_structure(h1)
    struct_score = 0
    struct_note = struct["type"]
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

    # ── 3. 15m Entry 评分 ──
    entry_score = 0
    entry_note = "无"
    if regime == "LONG" and struct["type"] == "UPTREND" and struct["structure_hold"]:
        if struct["hh_broken"]:
            entry_score = +1
            entry_note = f"HH突破({struct['last_HH']:.0f})"
        elif cur > struct["last_HL"] and cur < struct["last_HH"] * 0.98:
            entry_score = +0.5
            entry_note = f"HL回测({struct['last_HL']:.0f})"
    elif regime == "SHORT" and struct["type"] == "DOWNTREND" and struct["structure_hold"]:
        if cur < struct["last_HL"]:
            entry_score = -1
            entry_note = f"LL突破({struct['last_HL']:.0f})"
        elif cur > struct["last_HL"] * 1.02 and cur < struct["last_HH"]:
            entry_score = -0.5
            entry_note = f"LH回测({struct['last_HH']:.0f})"
    score += entry_score
    details["15m_Entry"] = {"value": entry_note, "score": entry_score}

    # ── 4. Market Quality Assessment ──
    quality = assess_market_quality(m15)
    quality_score = quality["score"]
    quality_state = quality["state"]
    details["MarketQuality"] = {"value": quality_state, "score": quality_score,
                                 "details": quality["details"]}

    # 低质量行情降权：质量<0.35时强制CHOP
    if quality_score < 0.35:
        return {
            "score": score, "direction": "CHOP", "type": "CHOP",
            "price": cur, "details": details,
            "reason": f"低质量行情({quality_state} {quality_score:.2f})，不交易",
            "market_quality": quality,
            "confidence": 0.0,
        }

    # ── 5. 最终裁决（带质量权重） ──
    raw_score = score

    # 质量权重：高质量(>0.6)=全重, NOISY(0.35~0.6)=衰减
    quality_mult = 1.0 if quality_state == "HIGH_QUALITY" else 0.7
    adjusted_score = raw_score * quality_mult

    if adjusted_score >= +3:
        direction = "LONG"
        signal_type = "HH_BREAKOUT" if entry_note.startswith("HH") else "HL_RETEST"
    elif adjusted_score <= -3:
        direction = "SHORT"
        signal_type = "LL_BREAKOUT" if entry_note.startswith("LL") else "LH_RETEST"
    else:
        direction = "CHOP"
        signal_type = "CHOP"

    # 结构破坏特殊处理
    if not struct["structure_hold"] and struct["type"] != "CHOP":
        direction = "EXIT"
        signal_type = "STRUCTURE_BROKEN"

    # ── 6. 模式历史置信度 ──
    pat_conf = get_pattern_confidence(signal_type) if direction != "CHOP" else 0.0
    # 模式置信度调节：<0.3时降级
    if direction != "CHOP" and pat_conf < 0.3 and PATTERN_MEMORY.get(signal_type, {}).get("t", 0) >= 3:
        direction = "CHOP"
        signal_type = "CHOP"

    # 综合置信度
    base_conf = min(abs(adjusted_score) / 6.0, 1.0)  # 满分6分为100%
    final_conf = round(base_conf * quality_score * (pat_conf + 0.5), 2)

    return {
        "score": raw_score,
        "adjusted_score": round(adjusted_score, 1),
        "direction": direction,
        "type": signal_type,
        "price": cur,
        "details": details,
        "reason": f"总评{raw_score:+d}(调后{adjusted_score:+.0f}): "
                  f"{details['4H_Regime']['value']}({details['4H_Regime']['score']:+d}) + "
                  f"{details['1H_Structure']['value']}({details['1H_Structure']['score']:+d})",
        "market_quality": quality,
        "pattern_memory": {"pattern": signal_type, "confidence": pat_conf} if direction != "CHOP" else {},
        "confidence": final_conf,
    }


def generate_mtf_signal(m15: list) -> dict | None:
    """
    MTF 信号生成（封装arbitrate）
    现改用 Decision Arbiter 统一裁决
    """
    arb = arbitrate(m15)
    if arb["direction"] == "CHOP":
        return None
    return arb

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
# Telegram 命令
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
                push("旺财MTF\n/eth /liq /signal /arbiter /stats /regime\n直接聊=AI分析", "")
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
                    sig = generate_mtf_signal(m15)
                    if sig:
                        mq = sig.get("market_quality", {})
                        mqs = mq.get("state", "?")
                        conf = sig.get("confidence", 0)
                        push(f"MTF {sig['direction']}\n置信: {conf:.0%} | 总评{sig['score']:+d}\n市场: {mqs}\n{sig['reason']}", "")
                    else:
                        # 完整诊断
                        arb = arbitrate(m15)
                        mq = arb.get("market_quality", {})
                        push(f"无信号\n总分{arb['score']:+d} | 质量:{mq.get('state','?')}({mq.get('score',0):.2f})\n{arb['reason']}", "")
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
                # AI 分析任何其他消息
                p, _, _, _ = price()
                r = think(f"用户问：{txt}\nETH${p:.0f}")
                push(r, ""); X["p"] = p; X["a"] = r
    except Exception as e:
        log(f"tg:{e}")

# ══════════════════════════════════════════════════════════════
# 主循环
# ══════════════════════════════════════════════════════════════

def main():
    global M, ST, SL, PATTERN_MEMORY
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
    log(f"旺财MTF上线 历史{ST['t']}单 胜率{ST['p']}%")
    log(f"架构: 4H Regime → 1H Structure → 15m Entry")
    if not os.path.exists("/root/trading_log.md"):
        with open("/root/trading_log.md", "w") as f: f.write("# 复盘\n")

    last_poll = 0
    while True:
        try: pass  # handle() disabled, HK handles TG
        except: pass

        t = time.time()
        if t - last_poll >= 60:
            last_poll = t
            try:
                verify()
                m15 = kl("15m", 200)
                if not m15 or len(m15) < 100: continue

                # ── MTF 信号扫描 ──
                sig = generate_mtf_signal(m15)
                if sig:
                    sig_id = f"{sig['type']}_{sig['direction']}"
                    global SIG_LAST, SIG_TIME
                    # 冷却：同类型15min内不重复
                    if sig_id != SIG_LAST or t - SIG_TIME > 900:
                        SIG_LAST = sig_id; SIG_TIME = t
                        emoji = "🟢" if sig["direction"] == "LONG" else ("🔴" if sig["direction"] == "SHORT" else "⚪")
                        mq = sig.get("market_quality", {})
                        mq_state = mq.get("state", "?")
                        conf = sig.get("confidence", 0)
                        title = f"{emoji} ETH {SIGNAL_CN.get(sig['type'], sig['type'])}"
                        body = (f"{sig['direction']} | 置信{conf:.0%}\n"
                                f"总评{sig['score']:+d} | 市场:{mq_state}\n"
                                f"${sig['price']:.0f}\n{sig['reason']}")
                        push(title, body)
                        log(f"SIG:{SIGNAL_CN.get(sig['type'],sig['type'])} {sig['direction']} conf={conf:.0%} mkt={mq_state} @${sig['price']:.0f}")
                        # 记录（含置信度和市场质量）
                        d = sig["direction"]
                        rg = "?"
                        for k, v in sig.get("details", {}).items():
                            if "Regime" in k:
                                rg = v.get("value", "?")
                        M.append({"t": t, "ts": time.strftime('%H:%M'), "p": sig['price'],
                                  "s": sig['type'], "a": sig['reason'],
                                  "d": d, "rg": f"4H:{rg}", "conf": conf})
                        if len(M) > 20: M = M[-20:]
                        with open(MF, "w") as f: json.dump(
                            {"M": M, "ST": ST, "SL": SL, "PM": PATTERN_MEMORY}, f, ensure_ascii=False)
            except Exception as e:
                log(f"cycle:{e}")
        time.sleep(1)

if __name__ == "__main__":
    main()
