#!/usr/bin/env python3
# =========================
# 旺财 v7.5 决策冻结架构
# ─────────────────────────────
# 数据流（单向，不可逆）:
#   data → indicators → state_machine → freeze_decision()
#        → risk_layer → execution_map → AI → PUSH
#
# 决策冻结点: freeze_decision() 之后任何字段不可修改
# 风控层: 纯约束（只否决，不改决策）
# 执行层: 纯翻译（不改任何东西）
# =========================

"""
旺财 - 主程序 (main.py)
==================
职责: 主循环 + Telegram命令处理 + 状态持久化

架构:
  main.py → data.py (数据)
          → indicators.py (指标)
          → state_machine.py (状态机)
          → execution.py (执行内核 + 总裁决层)
          → risk_layer.py (风险审核)
          → ai_layer.py (AI推理)
"""
import requests
import time
import json
import os
import sys

# ── 加载模块 ──────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import (
    get_candles, get_price, get_liquidation, get_funding_rate,
    get_oi, get_oi_volume, get_depth, get_long_short_ratio, push
)
from indicators import scan_signal, detect_structure
from state_machine import assess, should_push, decay_state, freeze_decision
from execution import direction_from_archetype
from risk_layer import check_risk, format_risk_report, ACCOUNT_SIZE
from ai_layer import think, build_prompt_from_decision

# ── 全局状态 ──────────────────────────────────────────
MEMORY_FILE = "/root/wangcai_memory.json"
TRADE_LOG   = "/root/trading_log.md"

# 全局变量
L = 0            # Telegram offset
M = []           # 交易记录
ST = {"t": 0, "w": 0, "l": 0, "p": 0, "s": 0}  # 战绩
RT = {}          # 分 regime 战绩
SL = {}          # 信号冷却
Sc = 0           # 推送计数
_S = {"env": "IDLE", "opp": "IDLE", "str": "", "id": "", "t": 0}  # 上次状态

# ── 日内风控追踪 ──────────────────────────────────────
TRADE_DAY = ""           # 记录当前日期（用于判断是否跨天）
DAILY_P = 0              # 日内累计盈亏U
TRADES_TODAY = 0         # 今日交易次数

# 健康检查
HL = {"t0": time.time(), "push": 0, "hold": 0,
      "err": 0, "block": 0, "states": {}}


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ═══════════════════════════════════════════════════════
# ❄️ Decision Arbiter（最终裁决器）v7.5.1
# ───────────────────────────────────────────────────────
# 职责：在 push 之前，最终确认 AI 输出与冻结决策一致
# 规则：
#   规则1：AI 输出方向与 decision 矛盾 → 截断 AI，用确定性模板
#   规则2：风险优先级最高，HIGH/VERY HIGH → 不发信号
#   规则3：观望 archetype → 移除仓位建议，只保留状态说明
# ═══════════════════════════════════════════════════════
def arbiter_check(decision: dict, ai_output: str) -> dict:
    """
    最终裁决：确保 Telegram 只有一种声音

    Returns:
        {"override": bool, "output": str, "reason": str}
    """
    archetype = decision.get("archetype", "-")
    opp = decision.get("opp", "IDLE")
    action = decision.get("action", "")
    risk_level = decision.get("risk_level", "MED")
    env = decision.get("env", "IDLE")
    hard_stop = decision.get("hard_stop", 0)
    pos_u = decision.get("position_u", 0)
    max_loss = decision.get("max_loss_u", 0)

    out = ai_output.lower()

    # ── 规则1：矛盾检测 ────────────────────────────
    # 判断决策方向
    decision_is_long = (
        opp in ("LONG_CONFIRM", "WATCH_LONG") or
        "做多" in action or "多头" in action or "LONG" in action or
        "反弹" in archetype or "顺势" in archetype or "突破" in archetype
    )
    decision_is_short = (
        opp in ("SHORT_CONFIRM", "WATCH_SHORT") or
        "做空" in action or "空头" in action or "SHORT" in action or
        "砸盘" in archetype or "放空" in archetype or "回调空" in archetype
    )

    # AI 声称的方向
    ai_claims_long = any(k in out for k in ["做多", "多头", "long", "买入", "开多", "看多"])
    ai_claims_short = any(k in out for k in ["做空", "空头", "short", "卖出", "开空", "看空", "杀多"])

    contradiction = False
    if decision_is_long and ai_claims_short and not ai_claims_long:
        contradiction = True
    if decision_is_short and ai_claims_long and not ai_claims_short:
        contradiction = True

    # ── 规则2：极高风险直接禁止 ────────────────────
    if risk_level in ("VERY HIGH",):
        return {
            "override": True,
            "output": (f"🔴 系统拦截：风险等级{risk_level}，当前不适合操作\n"
                       f"环境{env} | 信号{opp} | 等待更清晰信号"),
            "reason": f"risk_block:{risk_level}"
        }

    # ── 规则3：观望 archetype ─────────────────────
    if archetype in ("-", "关注") or opp == "IDLE":
        return {
            "override": True,
            "output": "⚪ 系统：观望等待\n当前信号不满足操作条件，等待下一信号。",
            "reason": "watch_idle"
        }

    # ── 矛盾时：截断AI，用确定性模板 ────────────────
    if contradiction:
        return {
            "override": True,
            "output": _deterministic_explain(decision),
            "reason": "direction_contradiction"
        }

    # 无矛盾：保留 AI 输出，但确保止损信息在第一行
    lines = ai_output.strip().split("\n")
    if lines and "止损" not in lines[0] and hard_stop > 0:
        lines[0] += f" | 止损 ${hard_stop:.0f}"
        ai_output = "\n".join(lines)

    return {"override": False, "output": ai_output, "reason": "pass"}


def _deterministic_explain(decision: dict) -> str:
    """确定性决策解释（矛盾时回退）"""
    action = decision.get("action", "")
    archetype = decision.get("archetype", "-")
    opp = decision.get("opp", "IDLE")
    period = decision.get("period", "")
    pos_u = decision.get("position_u", 0)
    max_loss = decision.get("max_loss_u", 0)
    hard_stop = decision.get("hard_stop", 0)
    env = decision.get("env", "IDLE")
    strg = decision.get("strg", "")

    opp_cn = {"LONG_CONFIRM":"确认做多","SHORT_CONFIRM":"确认做空",
               "WATCH_LONG":"关注做多","WATCH_SHORT":"关注做空",
               "REVERSAL_RISK":"反转风险","EXHAUSTION":"趋势衰竭",
               "IDLE":"无信号"}.get(opp, opp)
    strg_cn = {"STRONG":"强","MEDIUM":"中","WEAK":"弱"}.get(strg, strg)

    return (f"{action}\n"
            f"信号：{opp_cn}({strg_cn}) | 环境：{env}\n"
            f"仓位：{pos_u:.0f}U | 止损：${hard_stop:.0f} | 限亏：{max_loss:.0f}U\n"
            f"⚠️ 以上为系统唯一决策，不接受其他方向建议")


# ═══════════════════════════════════════════════════════
# 状态持久化
# ═══════════════════════════════════════════════════════
def load_state():
    global M, ST, RT, SL
    try:
        with open(MEMORY_FILE) as f:
            d = json.load(f)
        M = d.get("M", [])
        ST = d.get("ST", ST)
        RT = d.get("RT", {})
        SL = d.get("SL", {})
    except:
        M = []

    # 从历史恢复胜率
    for x in M:
        if x.get("r") == "W":
            ST["w"] += 1; ST["t"] += 1
        elif x.get("r") == "L":
            ST["l"] += 1; ST["t"] += 1
    ST["p"] = round(ST["w"] / max(ST["t"], 1) * 100, 1)


def save_state():
    with open(MEMORY_FILE, "w") as f:
        json.dump({"M": M, "ST": ST, "RT": RT, "SL": SL},
                  f, ensure_ascii=False)


def verify_trades():
    """结算超过2小时仍未标记的交易"""
    global ST, RT
    for x in M:
        if x.get("d") and not x.get("r") and time.time() - x["t"] > 7200:
            p, _, _, _ = get_price()
            if x["d"] == "LONG":
                x["r"] = "W" if p > x["p"] else "L"
            elif x["d"] == "SHORT":
                x["r"] = "W" if p < x["p"] else "L"
            if x.get("r"):
                ST[x["r"]] = ST.get(x["r"], 0) + 1
                ST["t"] += 1
                rg = x.get("rg", "UNKNOWN")
                dr = x["d"]
                if rg not in RT:
                    RT[rg] = {}
                if dr not in RT[rg]:
                    RT[rg][dr] = {"t": 0, "w": 0, "l": 0}
                RT[rg][dr]["t"] += 1
                RT[rg][dr][x["r"]] += 1

    ST["p"] = round(ST["w"] / max(ST["t"], 1) * 100, 1)
    ST["s"] = 0
    for x in reversed(M):
        if x.get("r") == "W":
            ST["s"] += 1
        elif x.get("r") == "L":
            break
    save_state()


# ═══════════════════════════════════════════════════════
# 风控辅助函数
# ═══════════════════════════════════════════════════════
def _calc_recent_drawdown() -> float:
    """
    计算近期最大回撤（U，正数）
    只看最近5笔交易
    """
    if len(M) < 2:
        return 0.0
    peak = 0.0
    max_dd = 0.0
    # 假设初始权益为 ACCOUNT_SIZE
    equity = float(ACCOUNT_SIZE)
    for x in M[-10:]:  # 看最近10笔
        # 简单估算每笔盈亏（用direction和价格变化）
        if x.get("r") == "W":
            equity += abs(x.get("p", 0) * 0.01)  # 简化估算
        elif x.get("r") == "L":
            equity -= abs(x.get("p", 0) * 0.01)
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


# ═══════════════════════════════════════════════════════
# 主扫描循环
# ═══════════════════════════════════════════════════════
def run_cycle():
    global Sc, _S, SL, TRADE_DAY, DAILY_P, TRADES_TODAY

    # ① 获取数据
    m15 = get_candles("15m", 30)
    h1  = get_candles("1h", 200)
    h4  = get_candles("4h", 200)
    if len(m15) < 25:
        return None

    # ② 扫描信号
    sig = scan_signal(m15, h1, h4)
    if not sig:
        return None

    # ③ 信号冷却
    sk = sig["s"][:4]
    if sk in SL and time.time() - SL[sk] < 300:
        return None
    SL[sk] = time.time()

    # ④ 结构识别
    st = detect_structure(m15)
    sig["st"] = st

    # ⑤ 获取上下文数据
    liq = get_liquidation()
    liq_str = f"多${liq['tl']/1e6:.0f}M/空${liq['ts']/1e6:.0f}M" \
              if liq.get("tl") else "无清算"
    frt  = get_funding_rate()
    oi   = get_oi()
    oiv  = get_oi_volume()
    ls   = get_long_short_ratio()
    dpt  = get_depth()

    # ⑥ 状态机评估
    env, opp, sys_s, strg, archetype = assess(sig, ls)
    state_id = f"{env}/{opp}/{strg}"

    # ⑦ 状态衰减检查
    t = time.time()
    prev_time = SL.get(f"st_{_S['opp']}_t", 0)
    if decay_state(_S["opp"], opp, prev_time, t, 3600):
        log(f"DECAY: {_S['opp']} expired, resetting")
        _S = {"env": "IDLE", "opp": "IDLE", "str": "", "id": "", "t": 0}

    # ⑧ 推送决策
    prev_env = _S.get("env", "IDLE")
    prev_opp = _S.get("opp", "IDLE")
    prev_strg = _S.get("str", "")

    ok_to_push = should_push(env, opp, strg, prev_env, prev_opp, prev_strg)

    if not ok_to_push:
        health("hold")
        log(f"HOLD: {state_id} (prev:{_S.get('id','')})")
        return None

    # ── ⑧ 环境否决（硬限制，在 freeze_decision 前） ─────
    if env == "CHAOS":
        health("block")
        log("BLOCKED: CHAOS环境，禁止所有交易")
        return None
    if opp == "IDLE" or archetype in ("-", "关注"):
        health("block")
        log("BLOCKED: 无有效信号")
        return None

    # ── ⑨ 决策冻结（唯一决策生成点） ─────────────────
    decision = freeze_decision(env, opp, strg, archetype, sig["p"])
    state_id = decision.get("decision_id", state_id)

    # ── 日内风控重置（跨天） ─────────────────────────
    today = time.strftime("%Y-%m-%d")
    if TRADE_DAY and TRADE_DAY != today:
        DAILY_P = 0
        TRADES_TODAY = 0
        log("NEW DAY: daily risk reset")
    TRADE_DAY = today

    # ── 计算近期最大回撤 ────────────────────────────
    recent_max_drawdown = _calc_recent_drawdown()

    # ── ⑩ 风控层审核（纯约束，不改决策） ─────────────
    risk_result = check_risk(
        decision_frozen=decision,
        daily_stats={"p": DAILY_P, "w": ST["w"], "l": ST["l"], "t": ST["t"]},
        trades_today=TRADES_TODAY,
        recent_max_drawdown=recent_max_drawdown,
    )

    if not risk_result["allowed"]:
        health("block")
        log(f"RISK BLOCKED: {risk_result['blocked_by']}")
        return None

    # ── ⑪ AI推理（决策解释，不重新分析） ─────────────────
    # ⚠️ v7.5.1: 只解释 freeze_decision()，不读原始市场数据
    ai_think = think(build_prompt_from_decision(decision, sig), 25)

    # ── ⑪½ Decision Arbiter（最终裁决） ──────────────────
    # ⚠️ v7.5.1: 确保 AI 输出不与冻结决策矛盾
    arbiter_verdict = arbiter_check(decision, ai_think)
    if arbiter_verdict["override"]:
        ai_think = arbiter_verdict["output"]
        log(f"ARBITER: AI输出被截断 → {arbiter_verdict['reason']}")

    # ── ⑫ 推送（使用冻结决策，全程不变） ─────────────
    emoji = "🔴" if env == "CHAOS" else "🟢" if "CONFIRM" in opp else "🟡"
    risk_report = format_risk_report(risk_result, decision)

    body = f"""{emoji}{decision['archetype']} ${sig['p']:.0f}
{decision['action']}
周期:{decision['period']} | 仓位:{decision['position']} | 风险:{decision['risk_level']}
止损:${decision['hard_stop']:.0f}
止盈:{decision['tp_note']}
{risk_report}

{ai_think}"""

    push(body, f"${sig['p']:.0f} {decision['archetype']} {decision['action'][:20]}")
    health("push")
    log(f"PUSH: {state_id} @{decision['archetype']} ${sig['p']:.0f}")

    # ── ⑬ 记录交易（使用冻结决策） ───────────────────
    direction = direction_from_archetype(decision['action'], opp)
    M.append({
        "t": t,
        "ts": time.strftime("%H:%M"),
        "p": sig["p"],
        "s": sig["s"],
        "a": ai_think,
        "d": direction,
        "rg": f"{env}/{opp}",
        "st": state_id,
        "pos_u": decision["position_u"],
        "max_loss_u": decision["max_loss_u"],
        "hard_stop": decision["hard_stop"],
        "decision_id": decision["decision_id"],
    })
    if len(M) > 20:
        M[:] = M[-20:]

    # ⑫ 更新日内追踪
    TRADES_TODAY += 1
    save_state()

    # ⑬ 更新状态
    _S = {"env": env, "opp": opp, "str": strg,
          "id": state_id, "t": t}
    SL[f"st_{opp}_t"] = t
    Sc += 1

    return decision


# ═══════════════════════════════════════════════════════
# 健康检查
# ═══════════════════════════════════════════════════════
def health(k: str = ""):
    if k == "push":
        HL["push"] += 1
    elif k == "hold":
        HL["hold"] += 1
    elif k == "err":
        HL["err"] += 1
    elif k == "block":
        HL["block"] += 1


def get_health() -> str:
    elapsed = (time.time() - HL["t0"]) / 60
    top_states = sorted(HL["states"].items(), key=lambda x: -x[1])[:3]
    states_str = " ".join(f"{s}:{n}" for s, n in top_states)
    return (f"旺财运行{elapsed:.0f}分 "
            f"推{HL['push']}抑{HL['hold']}"
            f"错{HL.get('err',0)}拦{HL.get('block',0)} "
            f"状态{states_str}")


# ═══════════════════════════════════════════════════════
# Telegram 命令处理
# ═══════════════════════════════════════════════════════
def handle_commands():
    global L
    TG_TOKEN = "8640664409:AAHPZIZd1YGa6jCwXziM01qoJ0RJwCfG-LM"
    TG_CHAT  = "8410098965"

    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": L + 1, "timeout": 10},
            timeout=15
        )
        if r.status_code != 200:
            return
        for upd in r.json().get("result", []):
            L = max(L, upd["update_id"])
            msg  = upd.get("message", {})
            cid  = msg.get("chat", {}).get("id")
            txt  = msg.get("text", "").strip()
            if not txt or str(cid) != TG_CHAT:
                continue
            log(f"CMD: {txt[:30]}")

            # /save - 记录复盘
            if txt in ("/save", "记录复盘"):
                with open(TRADE_LOG, "a", encoding="utf-8") as f:
                    from data import get_price
                    p, _, _, _ = get_price()
                    f.write(f"\n## {time.strftime('%m-%d %H:%M')} ${p:.0f}\n")
                push("复盘已存档", "")
                continue

            # /start
            if txt == "/start":
                push("旺财\n/eth /liq /signal /stats /save\n直接聊=AI分析", "")
                continue

            # /eth
            if txt == "/eth":
                p, _, lo, hi = get_price()
                if p:
                    push(f"ETH ${p:.0f} 24h${hi:.0f}~${lo:.0f}", "")
                else:
                    push("获取失败", "")
                continue

            # /liq
            if txt == "/liq":
                d = get_liquidation()
                if not d:
                    push("无清算", "")
                else:
                    rr = max(d["tl"], d["ts"]) / max(min(d["tl"], d["ts"]), 1)
                    m = f"清算 多${d['tl']/1e6:.0f}M/空${d['ts']/1e6:.0f}M 比1:{rr:.0f}"
                    if d.get("sc"):
                        m += f"\n空密${d['sc'][0]['p']}(${d['sc'][0]['a']/1e4:.0f}万)"
                    if d.get("lc"):
                        m += f"\n多密${d['lc'][0]['p']}(${d['lc'][0]['a']/1e4:.0f}万)"
                    push(m, "")
                continue

            # /signal
            if txt == "/signal":
                m15 = get_candles("15m", 30)
                h1  = get_candles("1h", 200)
                h4  = get_candles("4h", 200)
                s = scan_signal(m15, h1, h4)
                if s:
                    push(f"{s['s']} ${s['p']:.0f}", "")
                else:
                    push("无信号", "")
                continue

            # /stats
            if txt == "/stats":
                push(f"旺财 总{ST['t']} 胜{ST['w']} 负{ST['l']} "
                     f"率{ST['p']}%/{ST['s']}连胜", "")
                continue

            # /health
            if txt == "/health":
                push(get_health(), "")
                continue

            # /trace N
            if txt.startswith("/trace"):
                try:
                    n = int(txt.split()[1]) if len(txt.split()) > 1 else 1
                    if 1 <= n <= len(M):
                        x = M[-n]
                        msg_out = (f"#{x.get('ts','')} ${x.get('p',0):.0f} "
                                   f"信号:{x.get('s','')} "
                                   f"状态:{x.get('rg','-')} "
                                   f"方向:{x.get('d','-')}")
                        push(msg_out, "")
                    else:
                        push("序号超范围", "")
                except:
                    push("用法: /trace 编号", "")
                continue

            # AI聊天
            p, _, _, _ = get_price()
            from data import get_liquidation as gl
            from data import get_funding_rate as gfr
            from data import get_long_short_ratio as gls
            liq_str = gl()
            frt = gfr()
            ls = gls()
            env_cn = {"IDLE":"无信号","CHAOS":"混乱",
                      "TREND_BULL":"多头趋势","TREND_BEAR":"空头趋势",
                      "RANGE":"盘整"}
            opp_cn = {"IDLE":"无信号","LONG_CONFIRM":"确认做多",
                      "SHORT_CONFIRM":"确认做空","WATCH_LONG":"关注做多",
                      "WATCH_SHORT":"关注做空","REVERSAL_RISK":"反转风险",
                      "EXHAUSTION":"趋势衰竭"}
            env_t = env_cn.get(_S.get("env",""), _S.get("env",""))
            opp_t = opp_cn.get(_S.get("opp",""), _S.get("opp",""))
            strg  = _S.get("str", "")
            sys_s = ""

            pm = f"当前状态：环境{env_t} 机会{opp_t}({strg})\n用户问：{txt}\nETH${p:.0f}"
            r_ai = think(pm, 20)
            push(r_ai, "")

    except Exception as e:
        log(f"TG: {e}")


# ═══════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════
def main():
    global M, ST, RT, SL, _S, Sc, TRADE_DAY, DAILY_P, TRADES_TODAY

    log("旺财 v7.5 决策冻结架构 启动中...")

    # 加载状态
    load_state()
    log(f"历史 {ST['t']}单 胜率{ST['p']}% states:{len(SL)}")

    # 初始化日志
    if not os.path.exists(TRADE_LOG):
        with open(TRADE_LOG, "w", encoding="utf-8") as f:
            f.write("# 复盘\n")

    last_cycle = 0

    while True:
        # ① 处理命令
        try:
            handle_commands()
        except:
            pass

        # ② 每60秒扫描一次
        t = time.time()
        if t - last_cycle >= 60:
            last_cycle = t
            try:
                verify_trades()      # 结算超时交易
                run_cycle()           # 主扫描
            except Exception as e:
                log(f"CYCLE: {e}")
                health("err")

        time.sleep(1)


if __name__ == "__main__":
    main()
