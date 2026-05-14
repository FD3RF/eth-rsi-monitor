#!/usr/bin/env python3
import sys
sys.path.insert(0, "/root/wangcai")

from state_machine import assess, freeze_decision
from execution import decision_layer
from risk_layer import check_risk, format_risk_report
from data import get_candles, get_price, get_long_short_ratio
from indicators import scan_signal

print('=== 1. 数据 ===')
m15 = get_candles('15m', 30)
h1 = get_candles('1h', 200)
h4 = get_candles('4h', 200)
p = get_price()[0]
print(f'15m={len(m15)}根 ETH=${p:.0f}')

print('=== 2. 信号 ===')
sig = scan_signal(m15, h1, h4)
if sig:
    print(f'信号:{sig["s"]} RSI:{sig["r"]:.0f} VR:{sig["v"]:.1f}x')

print('=== 3. assess ===')
ls = get_long_short_ratio()
env, opp, sys_s, strg, archetype = assess(sig, ls)
print(f'{env}/{opp}/{strg}/{archetype}')

print('=== 4. freeze_decision ===')
decision = freeze_decision(env, opp, strg, archetype, p)
print(f'ID={decision["decision_id"]}')
print(f'仓位={decision["position"]}={decision["position_u"]:.0f}U')
print(f'限亏={decision["max_loss_u"]:.0f}U 硬止损=${decision["hard_stop"]:.0f}')

print('=== 5. check_risk ===')
risk = check_risk(decision, {'p':0,'w':0,'l':0,'t':0}, 0, 0.0)
print(f'allowed={risk["allowed"]} blocked={risk["blocked_by"]} warn={risk["warning"]}')

print('=== 6. report ===')
print(format_risk_report(risk, decision))
print('V75 FROZEN ARCH OK')
