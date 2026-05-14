#!/usr/bin/env python3
# =========================
# ❄️ FROZEN STATE MACHINE MODULE
# 物理级封锁声明
# ─────────────────────────────
# 这是系统唯一的大脑（SSOT）
# assess() = 唯一决策生成点
# freeze_decision() = 决策冻结点（生成后不可改）
# 其他模块：只读 / 约束 / 翻译
# =========================

"""
旺财 - 状态机层 (state_machine.py)
=====================
职责: 市场状态评估 + Archetype 识别 + 决策冻结

决策树:
  Layer1(环境) → Layer2(机会) → Layer3(Archetype) → freeze_decision()
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from execution import EXECUTION_TABLE


def assess(d: dict, ls_str: str) -> tuple:
    """
    三层状态评估（唯一决策函数）

    Args:
        d: scan_signal() 返回的信号字典
        ls_str: 多空比字符串

    Returns:
        (env, opp, sys_s, strg, archetype)
        env:      环境状态
        opp:      机会状态
        sys_s:    系统状态
        strg:     信号强度
        archetype: 行为类型
    """
    if d is None:
        return ("IDLE", "IDLE", "SILENT", "", "-")

    # ── 解包 ──
    rs = d["r"]          # RSI
    vr = d["v"]          # 成交量比率
    bw = d.get("bb", 0)  # 布林带宽度%
    h4 = d["h4"]         # 4H趋势
    h1 = d["h"]           # 1H趋势
    st = d.get("st", "无")  # 结构（扫多/扫空/无）
    sig = d["s"]         # 信号描述

    bear4 = "🔴" in h4
    bull4 = "🟢" in h4
    bear1 = "🔴" in h1
    bull1 = "🟢" in h1
    bear = bear4 or bear1
    bull = bull4 or bull1
    mix = (bear4 and bull1) or (bull4 and bear1)

    # ── 解析多空比 ──
    bn_ls = 2.0
    try:
        x = ls_str.replace("币安", "").replace("OKX", "")
        parts = x.split("/")
        if len(parts) >= 4:
            bn_ls = float(parts[0])
    except:
        pass
    ls_extreme = bn_ls > 2.5 or bn_ls < 1.5

    # ═══════════════════════════════════════════
    # Layer 1: 环境判断（6种状态）
    # ═══════════════════════════════════════════
    if bw > 8 and vr > 2:
        # CHAOS: 高波动 + 倍量（RSI极端条件移除，简化逻辑）
        # 注: 原版有 rs>80 or rs<20，但已在 RSI触发信号 时覆盖
        env = "CHAOS"
    elif bear and not mix:
        # TREND_BEAR: 空头趋势（合并原 EXPANSION）
        env = "TREND_BEAR"
    elif bull and not mix:
        # TREND_BULL: 多头趋势（合并原 EXPANSION）
        env = "TREND_BULL"
    else:
        # RANGE: 盘整（合并原 SQUEEZE）
        env = "RANGE"

    # ═══════════════════════════════════════════
    # Layer 2: 机会判断（7种状态）
    # ═══════════════════════════════════════════
    # 信号定义
    conf_bear = bear and (rs > 70 or "扫空" in st)
    conf_bull = bull and (rs < 30 or "扫多" in st)
    watch_bear = bear and rs > 65
    watch_bull = bull and rs < 35
    exh_bear = bear and rs < 25 and "无" in st
    exh_bull = bull and rs > 75 and "无" in st

    # 机会判断（优先级递减）
    opp = "IDLE"
    strg = ""

    if vr > 3 and rs > 75 and bear:
        opp = "REVERSAL_RISK"; strg = "STRONG"
    elif rs < 20 and vr > 2 and bull:
        opp = "EXHAUSTION"; strg = "STRONG"
    elif conf_bear and ls_extreme:
        opp = "SHORT_CONFIRM"; strg = "STRONG"
    elif conf_bull and ls_extreme:
        opp = "LONG_CONFIRM"; strg = "STRONG"
    elif conf_bear:
        opp = "SHORT_CONFIRM"; strg = "MEDIUM"
    elif conf_bull:
        opp = "LONG_CONFIRM"; strg = "MEDIUM"
    elif watch_bear:
        opp = "WATCH_SHORT"; strg = "MEDIUM" if rs > 70 else "WEAK"
    elif watch_bull:
        opp = "WATCH_LONG"; strg = "MEDIUM" if rs < 30 else "WEAK"
    elif exh_bear:
        opp = "EXHAUSTION"; strg = "WEAK"
    elif exh_bull:
        opp = "EXHAUSTION"; strg = "WEAK"
    elif vr > 2:
        opp = "WATCH_SHORT" if bear else "WATCH_LONG"; strg = "WEAK"
    elif st not in ("", "无", "-"):
        opp = "WATCH_SHORT" if bear else "WATCH_LONG"; strg = "WEAK"

    # ═══════════════════════════════════════════
    # Layer 3: 系统状态
    # ═══════════════════════════════════════════
    if env == "CHAOS":
        sys_s = "RISK_ONLY"
    elif opp in ("LONG_CONFIRM", "SHORT_CONFIRM") and strg == "STRONG":
        sys_s = "HIGH_CONFIDENCE"
    elif opp != "IDLE":
        sys_s = "NORMAL"
    else:
        sys_s = "SILENT"

    # ═══════════════════════════════════════════
    # Layer 4: Archetype（行为类型）
    # ═══════════════════════════════════════════
    # 决策规则：archetype 由 opp + 结构 + 环境 共同决定
    # 禁止在 execution 层重新评估这些条件
    if opp == "LONG_CONFIRM":
        if "扫多" in st or "扫空" in st:
            archetype = "反弹"        # 假突破后反弹
        elif bull4:
            archetype = "顺势"        # 多头趋势持仓
        elif env == "RANGE":
            archetype = "反弹"        # 震荡抄底
        else:
            archetype = "突破"        # 放量突破
    elif opp == "SHORT_CONFIRM":
        if "扫空" in st or "扫多" in st:
            archetype = "砸盘"       # 假突破后追空
        elif bear4:
            archetype = "顺势"       # 空头趋势持仓
        elif env == "RANGE":
            archetype = "回调空"      # 震荡做空
        else:
            archetype = "放空"        # 放量突破
    elif opp in ("REVERSAL_RISK", "EXHAUSTION"):
        archetype = "反转尝试"       # 反转信号
    elif "WATCH" in opp:
        archetype = "关注"           # 观望
    else:
        archetype = "-"

    return env, opp, sys_s, strg, archetype


def should_push(
    env: str,
    opp: str,
    strg: str,
    prev_env: str,
    prev_opp: str,
    prev_strg: str
) -> bool:
    """
    推送决策函数（唯一推送决策点）

    规则:
    1. IDLE → 不推送
    2. 状态改变 + 强度改变 + 机会改变 → 推送
    3. CHAOS → 强制禁止（非反转/衰竭）
    """
    if opp == "IDLE":
        return False

    # CHAOS 门控（P0禁止）
    if env == "CHAOS" and opp not in ("REVERSAL_RISK", "EXHAUSTION"):
        return False

    # 跨状态跃迁
    if prev_env != env or prev_opp != opp or prev_strg != strg:
        return True

    return False


def decay_state(prev_opp: str, opp: str, prev_time: float,
                current_time: float, cooldown: int = 3600) -> bool:
    """
    状态衰减判断

    Args:
        prev_opp: 上次机会状态
        current_time: 当前时间戳
        prev_time: 上次状态记录时间
        cooldown: 衰减周期（秒），默认1小时

    Returns:
        True → 状态已衰减，需要重置
    """
    if prev_opp != "IDLE" and current_time - prev_time > cooldown:
        return True
    return False


# ═══════════════════════════════════════════════════════
# ❄️ 决策冻结点（CRITICAL - 架构核心）
# ─────────────────────────────
# freeze_decision() 是决策链条的"不可变锚点"
# 一旦生成，以下字段全程不变：
#   direction / archetype / position / position_u
#   max_loss_u / hard_stop / sl_note / tp_note
#
# 下游模块只能：
#   ✅ 读取
#   ✅ 约束（否决）
#   ✅ 翻译（推送文本）
#
# 下游模块禁止：
#   ❌ 修改 position
#   ❌ 修改 hard_stop
#   ❌ 修改 direction
#   ❌ 重新评估市场
# ═══════════════════════════════════════════════════════

ACCOUNT_SIZE = 10000   # 与 risk_layer.py 保持一致
MAX_LOSS_PCT = 0.02    # 单笔最大亏损 2%
LEVERAGE = 20          # 杠杆倍数（用于硬止损计算）


def _calc_hard_stop(action: str, entry_price: float, max_loss_u: float) -> float:
    """根据最大亏损U计算硬止损价（冻结后不再重算）"""
    if max_loss_u <= 0 or entry_price <= 0:
        return 0.0
    loss_ratio = max_loss_u / ACCOUNT_SIZE
    price_move = entry_price * (loss_ratio / LEVERAGE)
    if "空" in action or "SHORT" in action:
        return entry_price + price_move   # 做空止损在更高价
    return entry_price - price_move       # 做多止损在更低价


def _position_u_from_label(position_label: str) -> float:
    """根据仓位标签换算U数额"""
    table = {
        "空仓": 0.0,
        "轻仓": 0.05,
        "中仓": 0.10,
        "重仓": 0.20,
        "中-重仓": 0.15,
        "轻仓试探": 0.05,
        "极轻仓": 0.03,
        "轻仓分批": 0.05,
    }
    for key, pct in table.items():
        if key in position_label:
            return ACCOUNT_SIZE * pct
    return ACCOUNT_SIZE * 0.05


def freeze_decision(env: str, opp: str, strg: str, archetype: str,
                    entry_price: float) -> dict:
    """
    决策冻结函数（CRITICAL - 唯一决策生成点）

    职责：
    1. 查询 EXECUTION_TABLE 生成执行协议
    2. 计算 position_u / max_loss_u / hard_stop
    3. 生成不可变决策包（冻结后全程不变）

    Args:
        env:        环境状态
        opp:        机会状态
        strg:       信号强度
        archetype:  行为类型
        entry_price: 入场价格

    Returns:
        dict 冻结决策包（全程不可修改）:
        {
            "env", "opp", "strg", "archetype",
            "action", "period", "position", "position_u",
            "max_loss_u", "hard_stop",
            "sl_note", "tp_note", "risk_level",
            "decision_id",  # 唯一标识，用于日志追踪
        }
    """
    # ── ① 查表获取执行协议 ────────────────────────────
    key = (archetype, strg)
    result = EXECUTION_TABLE.get(key)

    if result is None:
        result = EXECUTION_TABLE.get((archetype, ""))
        if result is None:
            result = ("⚪ 人工判断", "不确定", "轻仓或不操作",
                      "自行判断", "自行判断", "MED")

    action, period, position, sl_note, tp_note, risk_level = result

    # ── ② 计算仓位U ─────────────────────────────────
    position_u = _position_u_from_label(position)
    max_loss_u = position_u * MAX_LOSS_PCT

    # ── ③ 计算硬止损价 ────────────────────────────────
    hard_stop = _calc_hard_stop(action, entry_price, max_loss_u)

    # ── ④ 生成冻结决策包 ──────────────────────────────
    import time
    decision_id = f"{int(time.time())}_{archetype[:2]}"

    return {
        # 状态
        "env": env,
        "opp": opp,
        "strg": strg,
        "archetype": archetype,
        # 执行协议
        "action": action,
        "period": period,
        "position": position,
        "position_u": position_u,
        "max_loss_u": max_loss_u,
        "hard_stop": hard_stop,
        "sl_note": sl_note,
        "tp_note": tp_note,
        "risk_level": risk_level,
        # 标识
        "decision_id": decision_id,
    }
