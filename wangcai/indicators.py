#!/usr/bin/env python3
# =========================
# ❄️ FROZEN INDICATORS MODULE
# 物理级封锁声明
# ─────────────────────────────
# 禁止: 调用其他模块（data.py / state_machine.py / execution.py）
# 禁止: 做决策（只计算指标，不评估市场）
# 禁止: 返回None后偷偷补默认值（让调用方处理）
# =========================

"""
旺财 - 指标层 (indicators.py)
=====================
职责: 所有技术指标计算
设计原则: 指标只负责计算，不负责决策
"""
from typing import List, Dict

# ===================== EMA（指数移动平均） =====================
def calc_ema(data: List[Dict], period: int) -> float:
    """
    计算EMA
    Args:
        data: K线列表 [{"c":close, "h":high, "l":low, "v":volume}]
        period: 周期
    Returns:
        EMA值，或 None（数据不足）
    """
    if len(data) < period:
        return None
    k = 2 / (period + 1)
    r = sum(x["c"] for x in data[:period]) / period  # 初始SMA
    for x in data[period:]:
        r = x["c"] * k + r * (1 - k)
    return r


# ===================== RSI =====================
def calc_rsi(data: List[Dict], period: int = 14) -> float:
    """
    计算RSI（相对强弱指数）
    Returns: 0~100
    """
    if len(data) < period + 1:
        return 50
    g = l = 0
    for i in range(1, period + 1):
        z = data[i]["c"] - data[i - 1]["c"]
        if z > 0:
            g += z
        else:
            l -= z
    if l == 0:
        return 100
    return 100 - 100 / (1 + (g / period) / (l / period))


# ===================== SMA（简单移动平均） =====================
def calc_sma(data: List[Dict], period: int) -> float:
    """计算均量"""
    return sum(x["v"] for x in data[-period:]) / period


# ===================== 布林带宽度 =====================
def calc_bbw(data: List[Dict], period: int = 20, mult: int = 2) -> float:
    """
    计算布林带宽度（波动率指标）
    Returns: 宽度占均价的百分比
    """
    if len(data) < period:
        return 0
    s = [x["c"] for x in data[-period:]]
    ma = sum(s) / period
    variance = sum((x - ma) ** 2 for x in s) / period
    std = variance ** 0.5
    if ma == 0:
        return 0
    return (ma + mult * std - (ma - mult * std)) / ma * 100


# ===================== 结构识别 =====================
def detect_structure(data: List[Dict]) -> str:
    """
    识别市场结构（只识别两种核心结构，不做完整分析）

    决策权重: 仅用于增强/减弱CONFIRM信号，不单独触发

    Returns:
        "扫多" / "扫空" / "无"
    """
    if len(data) < 25:
        return ""

    try:
        c = data[-1]["c"]
        h = data[-1]["h"]
        l = data[-1]["l"]
        ph = data[-2]["h"]
        pl = data[-2]["l"]

        # 计算最近10根的区间
        rg = max(x["h"] for x in data[-10:]) - min(x["l"] for x in data[-10:])
        if rg == 0:
            return "无"

        # 扫多：下影线>30%区间 且 收盘在低点
        lower_shadow = min(c, pl) - l
        if lower_shadow > rg * 0.3 and c < pl:
            return "扫多"

        # 扫空：上影线>30%区间 且 收盘在高点
        upper_shadow = h - max(c, ph)
        if upper_shadow > rg * 0.3 and c > ph:
            return "扫空"

        return "无"
    except:
        return "-"


# ===================== 信号扫描 =====================
def scan_signal(m15: List[Dict], h1: List[Dict], h4: List[Dict]) -> Dict:
    """
    扫描触发信号（scan函数重构）

    SSOT触发条件（满足任一即返回信号）:
    1. RSI < 30（超卖）
    2. RSI > 70（超买）
    3. 倍量(VR>=2)
    4. EMA金叉/死叉

    Returns:
        {
          "s":  信号描述,
          "p":  当前价格,
          "r":  RSI,
          "v":  成交量比率,
          "e20": EMA20,
          "e60": EMA60,
          "h":  1H趋势标记(🟢/🔴/⚪),
          "h4": 4H趋势标记,
          "bb": 布林带宽度%,
          "vol": 当前成交量
        }
        或 None（无信号）
    """
    if len(m15) < 25:
        return None

    c = m15[-1]["c"]
    v = m15[-1]["v"]
    vs = calc_sma(m15, 20)
    vr = v / vs if vs > 0 else 0

    e20 = calc_ema(m15, 20)
    e60 = calc_ema(m15, 60)
    rs = calc_rsi(m15, 14)
    bw = calc_bbw(m15)

    # 1H趋势
    h1_t = "⚪"
    if len(h1) >= 170:
        e = calc_ema(h1, 144)
        el = calc_ema(h1, 169)
        if e and el:
            h1_t = "🟢" if e > el else "🔴"

    # 4H趋势
    h4_t = "⚪"
    if len(h4) >= 170:
        e = calc_ema(h4, 144)
        el = calc_ema(h4, 169)
        if e and el:
            h4_t = "🟢" if e > el else "🔴"

    # 触发信号
    sig = ""
    if rs < 30:
        sig = f"RSI超卖({rs:.0f})"
    elif rs > 70:
        sig = f"RSI超买({rs:.0f})"
    if not sig and vr >= 2:
        sig = f"倍量({vr:.1f}x)"

    # ❄️ 冻结: 禁止EMA交叉检测（原版有bug：引用未导入的data模块）
    # ❄️ 原因: EMA趋势由1H/4H趋势标记(h1/h4)提供，无需在15m重复检测

    if not sig:
        return None

    return {
        "s": sig, "p": c, "r": rs, "v": vr,
        "e20": e20, "e60": e60,
        "h": h1_t, "h4": h4_t,
        "bb": bw, "vol": v
    }
