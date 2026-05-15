#!/usr/bin/env python3
"""
旺财回测引擎 — 历史信号回测
用法: python3 backtest_wangcai.py [天数]
例: python3 backtest_wangcai.py 30  （回测最近30天）
"""
import requests, json, sys, time, os
sys.path.insert(0, "/root")
from wangcai import kl, aggregate_to_h1_h4, detect_1h_structure, get_regime
from wangcai import assess_market_quality, detect_15m_micro_structure, rsi
from wangcai import cascade_signal, arbitrate

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 7
INTERVAL = 15  # 分钟
BARS_PER_DAY = 24 * 4  # 15m = 96条/天
TOTAL_BARS = DAYS * BARS_PER_DAY + 200  # 多取一些用于计算指标

def fetch_historical():
    """获取历史K线数据"""
    # 单次最多取1000根, 分批次
    limit = min(TOTAL_BARS, 1000)
    bars = kl("15m", limit)
    if not bars:
        print("❌ 获取历史数据失败")
        return []
    print(f"已获取 {len(bars)} 根 15m K线 ({DAYS}天)")
    return bars

def run_backtest(bars):
    """逐根K线回测"""
    results = []
    wins = 0; losses = 0; total_pnl = 0.0
    max_dd = 0.0; peak = 0.0

    # 滑动窗口模拟
    for i in range(200, len(bars)):
        window = bars[:i+1]
        sig = cascade_signal(window)

        if sig and sig["direction"] in ("LONG", "SHORT"):
            entry_price = sig["price"]
            direction = sig["direction"]
            layer = sig.get("layer", 0)
            conf = sig.get("confidence", 0)

            # 模拟出场：持有到下一个反向信号或20根K线后
            exit_idx = min(i + 20, len(bars) - 1)
            exit_price = bars[exit_idx]["c"]

            if direction == "LONG":
                pnl_pct = (exit_price - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - exit_price) / entry_price * 100

            total_pnl += pnl_pct
            if pnl_pct > 0: wins += 1
            else: losses += 1

            peak = max(peak, total_pnl)
            dd = total_pnl - peak
            max_dd = min(max_dd, dd)

            results.append({
                "time": time.strftime('%m-%d %H:%M', time.localtime(time.time())),
                "price": entry_price,
                "type": sig["type"],
                "direction": direction,
                "layer": layer,
                "conf": conf,
                "pnl": round(pnl_pct, 2),
            })

    # 统计
    total = wins + losses
    win_rate = wins / total * 100 if total > 0 else 0
    avg_pnl = total_pnl / total if total > 0 else 0

    print(f"""
╔═══════════════════════════════════════╗
║       旺财回测报告 ({DAYS}天)          ║
╠═══════════════════════════════════════╣
║  总信号: {total:>5}                    ║
║  胜:     {wins:>5} ({win_rate:5.1f}%)              ║
║  负:     {losses:>5} ({100-win_rate:5.1f}%)           ║
║  总盈亏: {total_pnl:>+8.2f}%              ║
║  平均单笔: {avg_pnl:>+8.2f}%              ║
║  最大回撤: {max_dd:>8.2f}%                 ║
║  夏普(估): {total_pnl/max(-max_dd,1):>8.2f}                ║
╚═══════════════════════════════════════╝""")

    if results:
        print("\n最近10个信号:")
        print(f"{'时间':>12} {'价格':>8} {'类型':>14} {'方向':>6} {'层':>2} {'置信':>6} {'盈亏':>8}")
        print("-" * 60)
        for r in results[-10:]:
            print(f"{r['time']:>12} ${r['price']:>6.0f} {r['type']:>14} {r['direction']:>6} L{r['layer']:>1} {r['conf']:>5.0%} {r['pnl']:>+7.2f}%")

    return results

if __name__ == "__main__":
    print(f"⏳ 旺财回测引擎 v1.0 — 回测最近 {DAYS} 天...")
    bars = fetch_historical()
    if len(bars) > 200:
        run_backtest(bars)
    else:
        print("❌ 数据不足，至少需要200根K线")
