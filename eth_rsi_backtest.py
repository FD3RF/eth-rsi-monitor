#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETH RSI 监控回测器 - 用历史数据跑一遍所有逻辑，验证推送条件是否准确
"""
import requests, time, sys
from datetime import datetime

# 所有输出重定向到文件
LOG_FILE = r'C:\Users\Administrator\WorkBuddy\Claw\backtest_report.txt'
_ORIG_STDOUT = sys.stdout
log_f = open(LOG_FILE, 'w', encoding='utf-8')
sys.stdout = log_f
sys.stderr = log_f

GATEIO_API_URL = "https://api.gateio.ws/api/v4"
CONTRACT = "ETH_USDT"
RSI_PERIODS = [6, 14, 21]
LOW_THRESHOLD = 30; HIGH_THRESHOLD = 70
REQUEST_TIMEOUT = 15
VOL_LOOKBACK = 20; VOL_SURGE = 1.5; VOL_SHRINK = 0.5; PRICE_CHG_MIN = 0.5
BOLL_PERIOD = 20; BOLL_STD = 2
EMA_SHORT = 144; EMA_LONG = 169

# ---------- 跟生产代码完全一样的计算函数 ----------
def calc_rsi(p, period):
    if not p or len(p) < period + 1: return None
    g, l = [], []
    for i in range(1, len(p)):
        c = p[i] - p[i-1]
        if c > 0: g.append(c); l.append(0.0)
        else: g.append(0.0); l.append(abs(c))
    ag = sum(g[:period]) / period
    al = sum(l[:period]) / period
    for i in range(period, len(g)):
        ag = (ag * (period-1) + g[i]) / period
        al = (al * (period-1) + l[i]) / period
    return 100.0 - 100.0 / (1.0 + ag/al) if al > 1e-10 else 100.0

def calc_ema(p, period):
    if len(p) < period: return None
    m = 2 / (period + 1)
    r = sum(p[:period]) / period
    for x in p[period:]: r = (x - r) * m + r
    return r

def calc_boll(p, period=BOLL_PERIOD, std=BOLL_STD):
    if len(p) < period: return None, None, None
    s = sum(p[-period:]) / period
    v = sum((x - s)**2 for x in p[-period:]) / period
    sd = v ** 0.5
    return s, s + std * sd, s - std * sd

def fetch_history(interval, limit=1000):
    """拉取历史数据，返回 [(close, volume, timestamp), ...]"""
    try:
        r = requests.get(f"{GATEIO_API_URL}/futures/usdt/candlesticks",
            params={"contract": CONTRACT, "interval": interval, "limit": limit},
            timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list) or len(data) < 30:
            print(f"  数据不足: {len(data) if isinstance(data, list) else '非list'}")
            return None
        # 按时间正序排列（API默认最新的在前面？检查一下）
        # Gate.io 返回最新的在前面，我们反转让它按时间升序
        data.reverse()
        result = [(float(c['c']), float(c['v']), int(c['t'])) for c in data]
        return result
    except Exception as e:
        print(f"  拉取失败: {e}")
        return None

def run_single_check(c, v, iv, name):
    """在某个时间点执行一次检查，跟 run_period 逻辑一致，但不调 Bark"""
    alerts = []
    if len(c) < 22: return alerts

    r = {}
    for p in RSI_PERIODS:
        x = calc_rsi(c, p)
        if x is not None: r[p] = x
    if len(r) < 3: return alerts

    pr = c[-1]
    ema_s = calc_ema(c, EMA_SHORT)
    ema_l = calc_ema(c, EMA_LONG) if len(c) >= EMA_LONG else None
    bm, bu, bl = calc_boll(c)
    avg_v = (sum(v[-VOL_LOOKBACK-1:-1]) / VOL_LOOKBACK) if len(v) >= VOL_LOOKBACK + 2 else 1
    vr = v[-1] / avg_v if avg_v > 0 else 1
    pch = (c[-1] - c[-2]) / c[-2] * 100 if len(c) >= 2 else 0

    trend = []
    if ema_s and ema_l:
        if ema_s > ema_l: trend.append(f"EMA多头({ema_s:.0f}>{ema_l:.0f})")
        else: trend.append(f"EMA空头({ema_s:.0f}<{ema_l:.0f})")
        if pr > ema_s and pr > ema_l: trend.append("价格站上EMA")
        elif pr < ema_s and pr < ema_l: trend.append("价格在EMA下方")
        else: trend.append("价格在EMA之间")
    if bm:
        if pr > bu: trend.append("突破布林上轨")
        elif pr < bl: trend.append("跌破布林下轨")
        elif pr > bm: trend.append("布林中轨上方")
        else: trend.append("布林中轨下方")
    if vr > VOL_SURGE:
        trend.append("放量(%.1f倍)" % vr)
    elif vr < VOL_SHRINK:
        trend.append("缩量(%.2f倍)" % vr)
    tline = " | ".join(trend)

    # --- 1. BOLL突破 ---
    if bu and pr > bu:
        alerts.append(f"🔴 BOLL上轨突破 | 价格={pr:.1f} > 上轨={bu:.1f} | RSI={r[6]:.1f}/{r[14]:.1f}/{r[21]:.1f}")
    if bl and pr < bl:
        alerts.append(f"🟢 BOLL下轨突破 | 价格={pr:.1f} < 下轨={bl:.1f} | RSI={r[6]:.1f}/{r[14]:.1f}/{r[21]:.1f}")

    # --- 2. RSI低位 ---
    for p in RSI_PERIODS:
        if p in r and r[p] < LOW_THRESHOLD:
            ax = "RSI<30超卖" if r[6] < 30 else "RSI偏弱"
            if pr < bl: ax += "+布林下轨下方"
            elif ema_s and ema_l and pr < min(ema_s, ema_l): ax += "+EMA下方弱势"
            if vr > VOL_SURGE and pch < 0: ax += "+放量下跌确认空头"
            alerts.append(f"🟢 RSI低位(R{p})={r[p]:.1f} | {ax} | 价格={pr:.1f}")

    # --- 3. RSI高位 ---
    for p in RSI_PERIODS:
        if p in r and r[p] > HIGH_THRESHOLD:
            ax = "RSI>70超买" if r[6] > 70 else "RSI偏强"
            if pr > bu: ax += "+布林上轨上方"
            elif ema_s and ema_l and pr > max(ema_s, ema_l): ax += "+EMA上方强势"
            if vr < VOL_SHRINK: ax += "+缩量上涨注意背离"
            alerts.append(f"🔴 RSI高位(R{p})={r[p]:.1f} | {ax} | 价格={pr:.1f}")

    # --- 4. 量价 ---
    if len(v) >= 22:
        if vr > VOL_SURGE and pch < -PRICE_CHG_MIN:
            ax = "+RSI超卖" if r[6] < 30 else ""
            alerts.append(f"🔴 放量下跌 | 涨幅={pch:.2f}% 量比={vr:.1f}倍 {ax} | 价格={pr:.1f}")
        if vr < VOL_SHRINK and pch > PRICE_CHG_MIN * 0.6 and r[6] > 65:
            alerts.append(f"🟢 缩量上涨·背离 | 涨幅={pch:.2f}% 量比={vr:.2f}倍 RSI6={r[6]:.1f} | 价格={pr:.1f}")
        if vr > VOL_SURGE and abs(pch) < 0.2:
            alerts.append(f"🔴 放量不涨·顶部 | 量比={vr:.1f}倍 RSI6={r[6]:.1f} | 价格={pr:.1f}")

    if alerts:
        sno = f"[{alerts[0].split('|')[0].strip()}]"
        print(f"\n  ⚡ 触发 {len(alerts)} 条推送:")
        for a in alerts:
            cutoff = 120
            a_short = a if len(a) <= cutoff else a[:cutoff] + "..."
            print(f"    {a_short}")
    return alerts

def backtest(interval, name, limit=1000):
    print(f"\n{'='*70}")
    print(f"📊 {name} ({interval}) - 拉取 {limit} 根 K 线回测")
    print(f"{'='*70}")

    raw = fetch_history(interval, limit)
    if not raw:
        print("  ❌ 数据拉取失败")
        return

    closes = [x[0] for x in raw]
    volumes = [x[1] for x in raw]
    timestamps = [x[2] for x in raw]
    total = len(closes)

    # 打印数据范围
    start_dt = datetime.fromtimestamp(timestamps[0]).strftime('%Y-%m-%d %H:%M')
    end_dt = datetime.fromtimestamp(timestamps[-1]).strftime('%Y-%m-%d %H:%M')
    price_range = f"${min(closes):.1f} ~ ${max(closes):.1f}"
    print(f"  时间范围: {start_dt} → {end_dt}")
    print(f"  数据点数: {total}")
    print(f"  价格区间: {price_range}")
    print(f"  当前价格: ${closes[-1]:.1f}")

    # 从足够的预热期后开始跑
    warmup = max(EMA_LONG, 50)  # 需要足够的数据预热EMA
    print(f"  预热期: {warmup} 根 => 有效检查点: {total - warmup}")

    # 滑窗回放
    all_alerts = []
    alert_types = {"BOLL上轨": 0, "BOLL下轨": 0, "RSI低位": 0, "RSI高位": 0,
                   "放量下跌": 0, "缩量上涨": 0, "放量不涨": 0}

    # 打印进度：每10%显示一次
    step = max(1, (total - warmup) // 10)
    next_progress = step

    for i in range(warmup, total):
        window_c = closes[:i+1]
        window_v = volumes[:i+1]
        alerts = run_single_check(window_c, window_v, interval, name)
        if alerts:
            all_alerts.extend(alerts)
            for a in alerts:
                if "BOLL上轨" in a: alert_types["BOLL上轨"] += 1
                elif "BOLL下轨" in a: alert_types["BOLL下轨"] += 1
                elif "RSI低位" in a: alert_types["RSI低位"] += 1
                elif "RSI高位" in a: alert_types["RSI高位"] += 1
                elif "放量下跌" in a: alert_types["放量下跌"] += 1
                elif "缩量上涨" in a: alert_types["缩量上涨"] += 1
                elif "放量不涨" in a: alert_types["放量不涨"] += 1

        if i - warmup >= next_progress:
            pct = ((i - warmup) * 100) // (total - warmup)
            print(f"  进度: {pct}% ({i-warmup}/{total-warmup})", flush=True)
            next_progress += step

    # 汇总报告
    print(f"\n{'='*70}")
    print(f"📈 回测报告 - {name} ({interval})")
    print(f"{'='*70}")
    print(f"  时间范围:     {start_dt} → {end_dt}")
    print(f"  检查次数:     {total - warmup}")
    print(f"  触发总次数:   {len(all_alerts)}")
    print()
    has_trigger = False
    for k, v in alert_types.items():
        if v > 0:
            has_trigger = True
            print(f"  {k}: {v} 次")
    if not has_trigger:
        print("  (无任何条件触发)")

    # 当前状态快照
    print(f"\n{'='*70}")
    print(f"🔍 当前状态快照 (最新一根 K 线)")
    print(f"{'='*70}")
    run_single_check(closes, volumes, interval, name)
    print(f"\n  RSI: {calc_rsi(closes,6):.1f}/{calc_rsi(closes,14):.1f}/{calc_rsi(closes,21):.1f}")
    ema_s = calc_ema(closes, EMA_SHORT)
    ema_l = calc_ema(closes, EMA_LONG)
    print(f"  EMA({EMA_SHORT}): {ema_s:.1f}" if ema_s else "  EMA短: None")
    print(f"  EMA({EMA_LONG}): {ema_l:.1f}" if ema_l else "  EMA长: None")
    bm, bu, bl = calc_boll(closes)
    if bm: print(f"  BOLL: 中轨={bm:.1f} 上轨={bu:.1f} 下轨={bl:.1f}")
    print(f"  当前价格: ${closes[-1]:.1f}")

    return all_alerts

def main():
    print("=" * 60)
    print("  ETH RSI 监控系统 - 历史数据回测")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  合约: {CONTRACT}")
    print(f"  RSI周期: {RSI_PERIODS}  阈值: <{LOW_THRESHOLD} | >{HIGH_THRESHOLD}")
    print(f"  BOLL({BOLL_PERIOD},{BOLL_STD})  EMA({EMA_SHORT},{EMA_LONG})")
    print("=" * 60)

    # 先跑15m（数据量最小），快速验证
    # 再跑1h和4h
    for iv, nm in [("15m", "15分钟"), ("1h", "1小时"), ("4h", "4小时")]:
        try:
            backtest(iv, nm, limit=500)
        except Exception as e:
            print(f"\n  ❌ {nm} 回测失败: {e}")
        print()

    print("=" * 60)
    print("  回测完成!")
    print("=" * 60)

if __name__ == "__main__":
    main()
    log_f.close()
    _ORIG_STDOUT.write(f"\n日志已保存: {LOG_FILE}\n")
