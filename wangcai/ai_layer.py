#!/usr/bin/env python3
# =========================
# ❄️ FROZEN AI LAYER v7.5.1
# 修复版：只解释决策，不重新分析数据
# ─────────────────────────────
# ❄️ 铁律（违反即输出"参见系统决策"）:
# 1. 状态机方向 = 结论（必须服从，不得逆向修改）
# 2. 输出格式: 结论 + 证据 + 风险 + 动作
# 3. 必须给出止损价位或止损逻辑
# 4. 禁止矛盾句（不能同时看多又看空）
# 5. 风险只列事实，不做推测
# 6. IDLE/观望状态: 只输出"观望/等待"，不给交易条件
#
# ❄️ 决策权封锁:
# - 禁止建议修改仓位（轻/中/重仓）
# - 禁止建议修改止损/止盈
# - 禁止建议反方向
# - 禁止建议系统外交易
# → 系统已锁定：你的输出仅供参考，不影响实际执行
#
# ⚠️ v7.5.1 修复:
# - 不再接收原始 RSI/VR/EMA/清算 数据
# - 只接收 state_machine 的决策结论
# - AI 只能"解释"决策，不能"重新判断"市场
# =========================

"""
旺财 - AI推理层 (ai_layer.py)
=====================
职责: 决策解释器（只翻译 frozen_decision，不重新分析）

设计原则（铁律）:
- AI 不参与交易决策
- AI 只负责把 freeze_decision() 的结论翻译成人话
- AI 必须服从 state_machine 的结论，不能逆向修改
- ⚠️ v7.5.1: AI 只读决策，不读原始市场数据
"""

import requests
import json

# ===================== API配置 =====================
API_KEY = "sk-b2f5c0f817514e5bbf7ac7c4622f52e5"
API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

# ===================== 角色定义 =====================
# ⚠️ v7.5.1: 更严格的系统提示，禁止读原始数据
SYSTEM_PROMPT = """你是ETH狙击手旺财的决策解释器。

❄️ 你的职责（唯一）:
- 解释 state_machine 已做出的决策
- 把专业术语翻译成清晰的操作建议
- 补充关键风险点

❄️ 你必须服从的结论（不得逆向修改）:
- LONG_CONFIRM / 顺势 / 反弹 → 结论：做多（不得看空）
- SHORT_CONFIRM / 砸盘 / 放空 → 结论：做空（不得看多）
- WATCH_LONG / 关注 → 结论：观望（不得给具体仓位）
- WATCH_SHORT / 关注 → 结论：观望（不得给具体仓位）
- IDLE / 观望 → 结论：等待（不得建议开仓）

❄️ 输出规则:
1. 结论必须在第一句（与系统决策一致）
2. 证据：引用系统数据，不自创
3. 风险：只列系统风控已计算的事实
4. 动作：跟系统决策，不跟自己的判断
5. 禁止：同时看多又看空 / 建议修改止损 / 建议反向 / 建议系统外操作

❄️ 违者输出"参见系统决策"
⚠️ 注意：你看不到原始RSI/VR/EMA，你只能解释系统决策"""

# ===================== 状态翻译表 =====================
ENV_CN = {
    "IDLE": "无信号",
    "CHAOS": "混乱",
    "TREND_BULL": "多头趋势",
    "TREND_BEAR": "空头趋势",
    "RANGE": "盘整",
    "SQUEEZE": "压缩",
    "EXPANSION": "扩张"
}

OPP_CN = {
    "IDLE": "无信号",
    "LONG_CONFIRM": "确认做多",
    "SHORT_CONFIRM": "确认做空",
    "WATCH_LONG": "关注做多",
    "WATCH_SHORT": "关注做空",
    "REVERSAL_RISK": "反转风险",
    "EXHAUSTION": "趋势衰竭"
}

STR_CN = {
    "STRONG": "强",
    "MEDIUM": "中",
    "WEAK": "弱"
}

RISK_CN = {
    "LOW": "低",
    "LOW-MED": "偏低",
    "MED": "中",
    "HIGH": "高",
    "VERY HIGH": "极高"
}

# ===================== 决策解释映射（快速路径）=====================
# ⚠️ v7.5.1: 优先用确定性解释，AI 只处理复杂情况
DECISION_EXPLANATIONS = {
    # (archetype_prefix, opp_category) → (结论, 证据模板, 风险模板)
    ("顺势", "LONG"): {
        "conclusion": "✅ 系统决策：顺势做多",
        "evidence": "环境：{env_cn} | 信号：{opp_cn}({strg_cn}) | 止损：${hs:.0f}",
        "risk": "风控限亏 {max_loss:.0f}U | 仓位 {pos_u:.0f}U",
        "action": "执行 {action} | {period} | 止损 ${hs:.0f}",
    },
    ("顺势", "SHORT"): {
        "conclusion": "✅ 系统决策：顺势做空",
        "evidence": "环境：{env_cn} | 信号：{opp_cn}({strg_cn}) | 止损：${hs:.0f}",
        "risk": "风控限亏 {max_loss:.0f}U | 仓位 {pos_u:.0f}U",
        "action": "执行 {action} | {period} | 止损 ${hs:.0f}",
    },
    ("反弹", "LONG"): {
        "conclusion": "✅ 系统决策：反弹做多（快进快出）",
        "evidence": "环境：{env_cn} | 信号：{opp_cn} | RSI超买区域 | 止损：${hs:.0f}",
        "risk": "风控限亏 {max_loss:.0f}U | 高风险，快进快出",
        "action": "执行 {action} | 15M | 止损 ${hs:.0f} | RSI中性出",
    },
    ("砸盘", "SHORT"): {
        "conclusion": "✅ 系统决策：突破追空（动量）",
        "evidence": "环境：{env_cn} | 信号：{opp_cn} | 结构突破 | 止损：${hs:.0f}",
        "risk": "风控限亏 {max_loss:.0f}U | 极高风险，动量操作",
        "action": "执行 {action} | 止损 ${hs:.0f} | 快速止盈",
    },
    ("放空", "SHORT"): {
        "conclusion": "✅ 系统决策：放量做空",
        "evidence": "环境：{env_cn} | 信号：{opp_cn} | 放量 | 止损：${hs:.0f}",
        "risk": "风控限亏 {max_loss:.0f}U | 高风险",
        "action": "执行 {action} | RSI<40分批出",
    },
    ("回调空", "SHORT"): {
        "conclusion": "✅ 系统决策：回调做空",
        "evidence": "环境：{env_cn} | 信号：{opp_cn} | 回调结构 | 止损：${hs:.0f}",
        "risk": "风控限亏 {max_loss:.0f}U",
        "action": "执行 {action} | RSI中性(50)出",
    },
    ("突破", "LONG"): {
        "conclusion": "✅ 系统决策：放量突破做多",
        "evidence": "环境：{env_cn} | 信号：{opp_cn} | 放量突破 | 止损：${hs:.0f}",
        "risk": "风控限亏 {max_loss:.0f}U | 高风险",
        "action": "执行 {action} | RSI>65分批出",
    },
    ("反转尝试", ""): {
        "conclusion": "⚠️ 系统决策：反转尝试（高风险）",
        "evidence": "环境：{env_cn} | 信号：{opp_cn} | 反转信号",
        "risk": "风控限亏 {max_loss:.0f}U | 趋势反转未确认，极高风险",
        "action": "执行 {action} | 趋势确认后加仓/出",
    },
}


def build_prompt_from_decision(decision: dict, sig: dict = None) -> str:
    """
    ⚠️ v7.5.1 新接口：从 frozen_decision 构建 AI prompt
    不再接收原始市场数据，只从决策包构建

    Args:
        decision: freeze_decision() 返回的冻结决策包
        sig: 可选，原始信号字典（仅用于补充展示，不参与AI推理）
    """
    env = decision.get("env", "IDLE")
    opp = decision.get("opp", "IDLE")
    archetype = decision.get("archetype", "-")
    action = decision.get("action", "")
    period = decision.get("period", "")
    pos_u = decision.get("position_u", 0)
    max_loss = decision.get("max_loss_u", 0)
    hard_stop = decision.get("hard_stop", 0)
    risk_level = decision.get("risk_level", "MED")
    strg = decision.get("strg", "")

    env_cn = ENV_CN.get(env, env)
    opp_cn = OPP_CN.get(opp, opp)
    strg_cn = STR_CN.get(strg, strg)
    risk_cn = RISK_CN.get(risk_level, risk_level)

    # 判断opp类别
    is_long = opp in ("LONG_CONFIRM", "WATCH_LONG") or "LONG" in opp
    is_short = opp in ("SHORT_CONFIRM", "WATCH_SHORT") or "SHORT" in opp
    opp_cat = "LONG" if is_long else ("SHORT" if is_short else "")

    # 快速路径：确定性模板
    key = (archetype, opp_cat)
    if key in DECISION_EXPLANATIONS:
        tpl = DECISION_EXPLANATIONS[key]
        # 格式化模板
        evidence_str = tpl['evidence'].format(
            env_cn=env_cn, opp_cn=opp_cn, strg_cn=strg_cn,
            hs=hard_stop, max_loss=max_loss, pos_u=pos_u)
        risk_str = tpl['risk'].format(max_loss=max_loss, pos_u=pos_u)
        action_str = tpl['action'].format(
            action=action, period=period, hs=hard_stop)
        return f"""{tpl['conclusion']}

📊 证据: {evidence_str}

⚠️ {risk_str}

🎯 {action_str}"""

    # 兜底：通用解释
    if opp == "IDLE" or archetype in ("-", "关注"):
        return f"""⚪ 系统决策：观望等待

环境：{env_cn} | 信号：无确认机会
当前不建议开仓，等待更清晰信号。"""

    return f"""✅ 系统决策：{action}

环境：{env_cn} | 信号：{opp_cn}({strg_cn})
周期：{period} | 仓位：{pos_u:.0f}U | 风险：{risk_cn}
止损：${hard_stop:.0f} | 限亏：{max_loss:.0f}U"""


def think(prompt: str, timeout: int = 25) -> str:
    """
    调用DeepSeek推理（只解释，不决策）

    ⚠️ v7.5.1: prompt 现在是"决策解释"而非"市场分析"
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ]

    for attempt in range(2):
        try:
            r = requests.post(
                API_URL,
                json={
                    "model": "deepseek-v4-pro",
                    "messages": messages,
                    "max_tokens": 300
                },
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=timeout
            )
            if r.status_code != 200:
                continue

            msg = r.json()["choices"][0]["message"]
            content = msg.get("content", "").strip()

            # ⚠️ v7.5.1: 验证输出是否包含矛盾
            if _has_contradiction(content, prompt):
                # 矛盾 → 回退到确定性模板
                return _fallback_from_prompt(prompt)

            return content

        except Exception as e:
            if attempt < 1:
                import time
                time.sleep(1)

    return "AI不通，参见系统决策"


def _has_contradiction(ai_output: str, prompt: str) -> bool:
    """
    ⚠️ v7.5.1: 检测AI输出是否与系统决策矛盾
    如果矛盾返回True，使用确定性模板兜底
    """
    out = ai_output.lower()

    # 检测是否同时看多又看空
    has_long = any(k in out for k in ["做多", "多头", "long", "买入", "开多"])
    has_short = any(k in out for k in ["做空", "空头", "short", "卖出", "开空", "杀多"])

    if has_long and has_short:
        return True  # 矛盾

    # 检测是否在应该做多时说做空
    if "系统决策" in prompt:
        # 从prompt中提取系统决策方向
        if ("顺势做多" in prompt or "反弹" in prompt or
            "突破做多" in prompt or "确认做多" in prompt):
            if has_short and not has_long:
                return True
        if ("顺势做空" in prompt or "砸盘" in prompt or
            "放量做空" in prompt or "回调做空" in prompt or
            "确认做空" in prompt):
            if has_long and not has_short:
                return True

    return False


def _fallback_from_prompt(prompt: str) -> str:
    """矛盾时使用prompt本身作为确定性输出"""
    # prompt 就是确定性模板的结果，直接截取关键部分
    lines = prompt.strip().split("\n")
    if len(lines) >= 3:
        return "\n".join(lines[:3])
    return "参见系统决策"
