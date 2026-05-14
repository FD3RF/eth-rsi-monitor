#!/usr/bin/env python3
"""
旺财 - 执行内核 (execution.py)
=====================
职责: Archetype → 执行协议映射

设计原则（铁律）:
- execution_map() 只接收 (archetype, strength)
- 禁止在此函数内重新评估市场数据（vr/rs/价格）
- 所有市场判断必须在 state_machine.assess() 中完成
- 此函数是"查表"而非"决策"

执行协议结构:
    (action, period, position, sl_note, tp_note, risk_level)
"""


# =========================
# ❄️ FROZEN EXECUTION MODULE
# 物理级封锁声明（永久生效）
# ─────────────────────────────
# 允许访问: archetype + strength
# 禁止访问: RSI / EMA / PRICE / VR / OI / STRUCTURE / STATS
# 禁止行为: 重新评估市场 / 修改仓位 / 修改止损止盈
# 违者 = 系统不稳定根源
# =========================

# ═══════════════════════════════════════════════════════
# 执行协议查表（纯查表 = 无脑）
# 格式: {archetype}_{strength}: (action, period, position, sl, tp, risk)
# ═══════════════════════════════════════════════════════
EXECUTION_TABLE = {

    # ── 观望 ──────────────────────────────────────────
    ("-", ""):        ("⚪ 观望", "无", "空仓", "无", "无", "LOW"),
    ("关注", ""):     ("⚪ 关注", "无", "空仓", "无", "无", "LOW"),

    # ── 顺势做多 ──────────────────────────────────────
    ("顺势", "STRONG"):   ("🟢 趋势持仓LONG", "4H+", "中-重仓",
                           "结构破位止损", "移动止盈/Regime翻转出", "LOW"),
    ("顺势", "MEDIUM"):   ("🟢 趋势回调做多", "1H-4H", "中仓",
                           "结构破位止损", "前高附近减仓", "LOW-MED"),
    ("顺势", "WEAK"):     ("🟡 趋势弱多观察", "1H", "轻仓试探",
                           "前低破位止损", "RSI>65分批出", "MED"),

    # ── 顺势做空 ──────────────────────────────────────
    # (archetype="顺势" + opp="SHORT_CONFIRM" 在调用时映射为做空方向)

    # ── 反弹 ──────────────────────────────────────────
    ("反弹", "STRONG"):   ("🟡 强反弹LONG（快进快出）", "15M-1H", "轻仓",
                           "入场K低点止损", "RSI中性(50)出/结构破", "HIGH"),
    ("反弹", "MEDIUM"):   ("🟡 反弹LONG（scalp）", "15M", "轻仓",
                           "入场K低点止损", "RSI>60分批出", "HIGH"),
    ("反弹", "WEAK"):     ("🟡 弱反弹观望", "15M", "轻仓",
                           "入场K低点止损", "RSI>60出", "HIGH"),

    # ── 砸盘（空头结构突破） ───────────────────────────
    ("砸盘", "STRONG"):   ("🔴 突破SHORT（动量）", "15M-1H", "试探仓",
                           "突破K高点止损", "快速止盈/扩张结束", "VERY HIGH"),
    ("砸盘", "MEDIUM"):   ("🔴 突破SHORT（动量）", "15M-1H", "试探仓",
                           "突破K高点止损", "快速止盈", "VERY HIGH"),
    ("砸盘", "WEAK"):     ("🔴 突破SHORT（观望）", "15M", "极轻仓",
                           "前高止损", "快速止盈", "HIGH"),

    # ── 放空（空头量增） ───────────────────────────────
    ("放空", "STRONG"):   ("🔴 放量SHORT", "1H", "中仓",
                           "前高止损", "RSI<40分批出", "HIGH"),
    ("放空", "MEDIUM"):   ("🔴 放量SHORT", "15M-1H", "轻仓",
                           "前高止损", "RSI<40出", "HIGH"),
    ("放空", "WEAK"):     ("🔴 放量空观察", "15M", "极轻仓",
                           "前高止损", "RSI<40出", "MED"),

    # ── 回调空 ─────────────────────────────────────────
    ("回调空", "STRONG"):  ("🔴 强回调SHORT（快进快出）", "15M-1H", "轻仓",
                            "入场K高点止损", "RSI中性(50)出/结构破", "HIGH"),
    ("回调空", "MEDIUM"):  ("🔴 回调SHORT（scalp）", "15M", "轻仓",
                            "入场K高点止损", "RSI<40分批出", "HIGH"),
    ("回调空", "WEAK"):    ("🔴 弱回调空观望", "15M", "极轻仓",
                            "入场K高点止损", "RSI<40出", "MED"),

    # ── 突破（多头量增） ───────────────────────────────
    ("突破", "STRONG"):   ("🟢 放量LONG", "1H", "中仓",
                           "入场K低点止损", "RSI>65分批出", "HIGH"),
    ("突破", "MEDIUM"):   ("🟢 放量LONG", "15M-1H", "轻仓",
                           "入场K低点止损", "RSI>65出", "HIGH"),
    ("突破", "WEAK"):     ("🟢 放量多观察", "15M", "极轻仓",
                           "入场K低点止损", "RSI>65出", "MED"),

    # ── 反转尝试 ───────────────────────────────────────
    ("反转尝试", "STRONG"): ("🟡 反转尝试（高风险）", "1H-4H", "轻仓分批",
                             "结构破坏止损", "趋势确认后加仓/出", "HIGH"),
    ("反转尝试", "MEDIUM"): ("🟡 反转尝试（高风险）", "1H", "轻仓",
                             "结构破坏止损", "趋势确认后出", "HIGH"),
    ("反转尝试", "WEAK"):   ("🟡 反转尝试（观望）", "1H", "极轻仓",
                             "结构破坏止损", "趋势确认后出", "MED"),
}


def execution_map(archetype: str, strength: str) -> tuple:
    """
    Archetype → 执行协议（确定性查表）

    Args:
        archetype: 行为类型（来自 state_machine.assess()）
        strength: 信号强度（STRONG / MEDIUM / WEAK）

    Returns:
        (action, period, position, sl_note, tp_note, risk_level)
    """
    key = (archetype, strength)
    result = EXECUTION_TABLE.get(key)

    if result is None:
        # 降级查表：只匹配 archetype
        result = EXECUTION_TABLE.get((archetype, ""))
        if result is None:
            # 最保守兜底
            return ("⚪ 人工判断", "不确定", "轻仓或不操作",
                    "自行判断", "自行判断", "MED")

    return result


def direction_from_archetype(archetype: str, opp: str) -> str:
    """
    根据 archetype + opp 判断交易方向
    用于记录历史战绩
    """
    if "做空" in archetype or "空" in archetype:
        return "SHORT"
    elif "多" in archetype or "LONG" in archetype:
        return "LONG"
    elif "反弹" in archetype or "突破" in archetype:
        return "LONG"
    elif "砸盘" in archetype or "放空" in archetype or "回调空" in archetype:
        return "SHORT"
    return ""


# ═══════════════════════════════════════════════════════
# ❄️ FROZEN 总裁决层（纯翻译版）
# ─────────────────────────────
# ❄️ 架构变更（v7.5）：否决逻辑移至 main.py（环境层硬限制）
#    execution_layer 只负责翻译，不否决
# ❄️ 绝对禁止: 访问历史战绩(stats)来调整仓位
# ╄️ 绝对禁止: 重新评估市场数据
# ═══════════════════════════════════════════════════════

def decision_layer(env: str, opp: str, archetype: str,
                   strength: str) -> dict:
    """
    总裁决层（纯翻译，不否决）

    架构说明:
    - 否决逻辑（CHAOS/IDLE）由 main.py 在调用 freeze_decision() 前处理
    - 此函数只负责翻译 archetype → 执行协议

    ❄️ 禁止: 访问stats调整仓位 / 重新评估RSI/VR/EMA/结构
    """
    # ── 调用执行内核（纯查表，不否决） ───────────────
    execution = execution_map(archetype, strength)

    return {
        "allowed": True,      # 不否决，由 main.py 统一处理
        "execution": execution,
        "action": execution[0],
        "reason": "通过"
    }
