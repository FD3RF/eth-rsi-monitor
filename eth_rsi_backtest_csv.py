#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETH RSI 监控完整回测 - 加载 CSV 数据跑全部逻辑 (优化版)
限制回看窗口到 300 根K线，避免 O(n²) 性能问题
"""
import csv, sys, time
from datetime import datetime

CSV_PATH = r'C:\Users\Administrator\AppData\Local\Temp\workbuddy-weixin-media\inbound\weixin-file-77356a03e82b9627-ETHUSDT_PERP_15m_last_1y.csv'
OUT_PATH = r'C:\Users\Administrator\WorkBuddy\Claw\backtest_1year_report.txt'
LOOKBACK_MAX = 300  # 回看窗口上限，足够 RSI21 + EMA169 的稳定计算

f = open(OUT_PATH, 'w', encoding='utf-8')
_stdout = sys.stdout
sys.stdout = f
sys.stderr = f

RSI_PERIODS = [6, 14, 21]
LOW_THRESHOLD = 30; HIGH_THRESHOLD = 70
VOL_LOOKBACK = 20; VOL_SURGE = 1.5; VOL_SHRINK = 0.5; PRICE_CHG_MIN = 0.5
BOLL_PERIOD = 20; BOLL_STD = 2
EMA_SHORT = 144; EMA_LONG = 169

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

def check(c, v):
    """单次检查，返回信号类别列表"""
    signals = []
    if len(c) < 22: return signals

    r = {}
    for p in RSI_PERIODS:
        x = calc_rsi(c, p)
        if x is not None: r[p] = x
    if len(r) < 3: return signals

    pr = c[-1]
    ema_s = calc_ema(c, EMA_SHORT)
    ema_l = calc_ema(c, EMA_LONG) if len(c) >= EMA_LONG else None
    bm, bu, bl = calc_boll(c)
    avg_v = (sum(v[-VOL_LOOKBACK-1:-1]) / VOL_LOOKBACK) if len(v) >= VOL_LOOKBACK + 2 else 1
    vr = v[-1] / avg_v if avg_v > 0 else 1
    pch = (c[-1] - c[-2]) / c[-2] * 100 if len(c) >= 2 else 0

    if bu and pr > bu:
        signals.append("BOLL上轨")
    if bl and pr < bl:
        signals.append("BOLL下轨")
    for p in RSI_PERIODS:
        if p in r and r[p] < LOW_THRESHOLD:
            signals.append("RSI低位")
    for p in RSI_PERIODS:
        if p in r and r[p] > HIGH_THRESHOLD:
            signals.append("RSI高位")
    if len(v) >= 22:
        if vr > VOL_SURGE and pch < -PRICE_CHG_MIN:
            signals.append("放量下跌")
        if vr < VOL_SHRINK and pch > PRICE_CHG_MIN * 0.6 and r[6] > 65:
            signals.append("缩量上涨背离")
        if vr > VOL_SURGE and abs(pch) < 0.2:
            signals.append("放量不涨")
    return signals

def main():
    print("=" * 70)
    print("  ETH RSI 监控系统 - 1年15m数据完整回测")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  数据: 15分钟K线, 2025-04-21 → 2026-04-21")
    print(f"  RSI{RSI_PERIODS}  <{LOW_THRESHOLD} | >{HIGH_THRESHOLD}")
    print(f"  BOLL({BOLL_PERIOD},{BOLL_STD})  EMA({EMA_SHORT},{EMA_LONG})")
    print(f"  回看窗口: {LOOKBACK_MAX}")
    print("=" * 70)

    # 加载
    print("\n📥 加载数据...", end=" ", flush=True)
    with open(CSV_PATH, 'r') as cf:
        rows = list(csv.DictReader(cf))
    print(f"{len(rows)} 根K线")

    closes = [float(r['close']) for r in rows]
    volumes = [float(r['volume']) for r in rows]
    timestamps = [r['open_time_utc'] for r in rows]
    total = len(closes)

    print(f"  范围: {timestamps[0]} → {timestamps[-1]}")
    print(f"  价格: ${min(closes):.2f} ~ ${max(closes):.2f}  当前: ${closes[-1]:.2f}")

    warmup = max(EMA_LONG, 50)
    num_checks = total - warmup
    print(f"  预热: {warmup}  检查点: {num_checks:,}")
    print()

    # 统计
    stats = {"BOLL上轨":0,"BOLL下轨":0,"RSI低位":0,"RSI高位":0,
             "放量下跌":0,"缩量上涨背离":0,"放量不涨":0}
    total_sigs = 0

    # 进度
    t0 = time.time()
    report_every = max(1, num_checks // 20)
    next_report = report_every

    for i in range(warmup, total):
        # 限制回看窗口
        start = max(0, i - LOOKBACK_MAX)
        c = closes[start:i+1]
        v = volumes[start:i+1]

        sigs = check(c, v)
        if sigs:
            total_sigs += len(sigs)
            for s in sigs:
                stats[s] = stats.get(s, 0) + 1

        pos = i - warmup
        if pos >= next_report:
            pct = pos * 100 // num_checks
            elapsed = time.time() - t0
            rate = pos / elapsed if elapsed > 0 else 0
            eta = (num_checks - pos) / rate if rate > 0 else 0
            print(f"  {pct:3d}% ({pos:,}/{num_checks:,})  "
                  f"信号={total_sigs}  "
                  f"速率={rate:.0f}点/s  "
                  f"剩余≈{eta:.0f}s")
            next_report += report_every

    elapsed = time.time() - t0
    print(f"  100% ({num_checks:,}/{num_checks:,}) 完成!")

    # ====== 报告 ======
    print()
    print("=" * 70)
    print("📊 回测报告 · 15分钟 · 1年数据")
    print("=" * 70)
    print(f"  时间范围:  {timestamps[0]} → {timestamps[-1]}")
    print(f"  总K线数:   {total:,}")
    print(f"  检查次数:   {num_checks:,}")
    print(f"  总信号数:   {total_sigs:,}")
    print(f"  信号频率:   {total_sigs/num_checks*100:.1f}%")
    print(f"  耗时:       {elapsed:.1f}秒")
    print()

    # 表格
    print("  ┌──────────────────────┬───────────┬──────────┐")
    print("  │ 信号类型             │ 触发次数  │ 触发率   │")
    print("  ├──────────────────────┼───────────┼──────────┤")
    cats = sorted(stats.items(), key=lambda x: -x[1])
    for cat, cnt in cats:
        rate = cnt / num_checks * 100
        print(f"  │ {cat:20s} │ {cnt:9d} │ {rate:6.3f}% │")
    print("  └──────────────────────┴───────────┴──────────┘")
    print()

    # 当前快照
    print("─" * 70)
    print("🔍 当前快照 (最后K线: %s)" % timestamps[-1])
    print("─" * 70)
    r6 = calc_rsi(closes, 6)
    r14 = calc_rsi(closes, 14)
    r21 = calc_rsi(closes, 21)
    print(f"  RSI: {r6:.1f}/{r14:.1f}/{r21:.1f}")
    print(f"  EMA({EMA_SHORT}): {calc_ema(closes,EMA_SHORT):.1f}")
    print(f"  EMA({EMA_LONG}): {calc_ema(closes,EMA_LONG):.1f}")
    bm, bu, bl = calc_boll(closes)
    print(f"  BOLL: 中={bm:.1f} 上={bu:.1f} 下={bl:.1f}")
    print(f"  价格: ${closes[-1]:.2f}")
    print()

    # 月度趋势
    print("─" * 70)
    print("📈 月度趋势")
    print("─" * 70)
    from collections import defaultdict
    monthly = defaultdict(list)
    for r in rows:
        monthly[r['open_time_utc'][:7]].append(float(r['close']))
    print(f"  {'月份':>8s} {'开盘':>8s} {'最高':>8s} {'最低':>8s} {'收盘':>8s} {'涨跌':>8s}")
    print(f"  {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    prev = None
    for m in sorted(monthly.keys()):
        p = monthly[m]
        op, hi, lo, cl = p[0], max(p), min(p), p[-1]
        chg = (cl-prev)/prev*100 if prev else 0
        sym = "▲" if chg > 0 else "▼"
        print(f"  {m:>8s} ${op:>6.1f} ${hi:>6.1f} ${lo:>6.1f} ${cl:>6.1f} {sym}{abs(chg):>5.2f}%")
        prev = cl
    print()

    print("=" * 70)
    print("  回测完成!")
    print(f"  报告: {OUT_PATH}")
    print("=" * 70)

if __name__ == "__main__":
    main()
    f.close()
    _stdout.write(f"\n✅ 回测完成! 报告: {OUT_PATH}\n")
