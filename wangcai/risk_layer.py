#!/usr/bin/env python3
"""
旺财 - 风险层 (risk_layer.py)
=====================
职责: 账户级风险约束（纯否决，不改决策）

架构位置:
  freeze_decision() → risk_layer.check_risk() → execution_map() → 推送

设计原则（铁律）:
- risk_layer 只有否决权（constraint gate）
- ❄️ 禁止修改 decision_frozen 的任何字段（position / hard_stop / archetype）
- hard_stop 在 state_machine.freeze_decision() 中计算一次，冻结后不再重算
- 所有修正必须可回溯，有明确日志
"""

# ═══════════════════════════════════════════════════════
# 风险参数（约束边界）
# ═══════════════════════════════════════════════════════
ACCOUNT_SIZE   = 10000   # U
MAX_LOSS_PCT   = 0.02    # 单笔最大亏损 2% = 200U
DAILY_LOSS_PCT = 0.06    # 日内最大累计亏损 6% = 600U
MAX_TRADES_PER_DAY = 5    # 日内最大交易次数


# ═══════════════════════════════════════════════════════
# ❄️ 核心风控函数（纯约束版）
# ═══════════════════════════════════════════════════════
def check_risk(
    decision_frozen: dict,
    daily_stats: dict,
    trades_today: int,
    recent_max_drawdown: float = 0.0,
) -> dict:
    """
    风险审核（risk_layer 唯一入口 - 纯约束版）

    架构原则:
    - 只否决，不修改 decision_frozen 的任何字段
    - hard_stop 由 state_machine.freeze_decision() 计算，此处只读
    - position 由 freeze_decision() 冻结，此处只读

    接收:
        decision_frozen: 冻结决策包（来自 state_machine.freeze_decision）
        daily_stats: {"p":盈亏U, "w":胜, "l":负, "t":总数}
        trades_today: 今日已交易次数
        recent_max_drawdown: 近期最大回撤U（正数）

    返回:
        {
          "allowed": bool,        # 是否允许开仓
          "blocked_by": str,       # 被什么拦截（空=通过）
          "warning": str,          # 警告信息（建议性，不阻止）
          "drawdown_pct": float,   # 回撤占账户比例（用于推送）
        }
    """
    blocked_by = ""
    warning = ""

    # ── ① 日内亏损否决（P0） ─────────────────────────
    daily_loss = abs(min(daily_stats.get("p", 0), 0))
    if daily_loss >= ACCOUNT_SIZE * DAILY_LOSS_PCT:
        return {
            "allowed": False,
            "blocked_by": "daily_loss",
            "warning": "",
            "drawdown_pct": daily_loss / ACCOUNT_SIZE,
        }

    # ── ② 日内交易次数否决（P0） ──────────────────────
    if trades_today >= MAX_TRADES_PER_DAY:
        return {
            "allowed": False,
            "blocked_by": "max_trades",
            "warning": "",
            "drawdown_pct": daily_loss / ACCOUNT_SIZE,
        }

    # ── ③ 回撤警告（不阻止，只提示） ─────────────────
    dd_pct = recent_max_drawdown / ACCOUNT_SIZE
    if dd_pct >= 0.05:
        warning = f"⚠️ 回撤{recent_max_drawdown:.0f}U({dd_pct*100:.1f}%)，关注风险"
    elif dd_pct >= 0.03:
        warning = f"⚡ 回撤{recent_max_drawdown:.0f}U({dd_pct*100:.1f}%)"

    return {
        "allowed": True,
        "blocked_by": "",
        "warning": warning,
        "drawdown_pct": dd_pct,
    }


def format_risk_report(risk_result: dict, decision_frozen: dict) -> str:
    """
    生成风控报告文本（纯展示）

    注意：hard_stop 和 position 来自 decision_frozen，全程不变
    """
    if not risk_result["allowed"]:
        blocked_cn = {
            "daily_loss": "日内亏损达上限",
            "max_trades": "日内交易次数达上限",
        }.get(risk_result["blocked_by"], risk_result["blocked_by"])
        return f"🔴 风控拦截: {blocked_cn}"

    # 从冻结决策中读取（全程不变）
    hs = decision_frozen.get("hard_stop", 0)
    pos_u = decision_frozen.get("position_u", 0)
    max_loss = decision_frozen.get("max_loss_u", 0)
    hs_str = f"${hs:.0f}" if hs else "无"

    report = f"✅ 风控通过 仓位{pos_u:.0f}U 止损{hs_str} 限亏{max_loss:.0f}U"
    if risk_result.get("warning"):
        report += f"\n{risk_result['warning']}"
    return report
