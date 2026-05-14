#!/usr/bin/env python3
"""Wangcai 15M Scanner - with AI analysis"""
import sys, os, time, requests
sys.path.insert(0, "/root")
from tg_bot import ask_agent, get_eth_price, scan_liquidation, format_liquidation, conflict_resolver, get_regime_tag, log_cr

_prev_e20 = None
_prev_e60 = None
_prev_c = None
_prev_r = None
_cooldown = {}

def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)

def tg(t):
    for _ in range(3):
        try:
            r = requests.post("https://api.telegram.org/bot8640664409:AAHPZIZd1YGa6jCwXziM01qoJ0RJwCfG-LM/sendMessage",
                json={"chat_id": "8410098965", "text": t[:2000]}, timeout=10)
            if r.status_code == 200: return
        except:
            pass

def klines(interval, limit):
    url = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=ETH_USDT&interval=%s&limit=%d" % (interval, limit)
    r = requests.get(url, timeout=10)
    if r.status_code != 200: return []
    return [{"c":float(x["c"]),"h":float(x["h"]),"l":float(x["l"]),"v":float(x["v"])} for x in r.json()]

def ema(data, p):
    k = 2/(p+1); e = data[0]["c"]
    for i in range(1,len(data)): e = data[i]["c"]*k + e*(1-k)
    return e

def rsi(data, p=14):
    if len(data) < p+1: return 50
    g=l=0
    for i in range(1,p+1):
        d = data[i]["c"]-data[i-1]["c"]
        if d>0: g+=d
        else: l-=d
    if l==0: return 100
    return 100 - 100/(1+(g/p)/(l/p))

def sma(data, p):
    return sum(c["v"] for c in data[-p:])/p

last_scan_time = 0

def scan():
    global _prev_e20,_prev_e60,_prev_c,_prev_r,last_scan_time
    b = klines("15m", 30)
    if len(b) < 25: return
    c = b[-1]["c"]; v = b[-1]["v"]
    e20 = ema(b,20); e60 = ema(b,60); rs = rsi(b,14); vs = sma(b,20)
    vr = v/vs if vs>0 else 0
    sig_type = ""

    if _prev_e20 and _prev_e60:
        if _prev_e20 <= _prev_e60 and e20 > e60: sig_type = "EMA金叉 多头启动"
        elif _prev_e20 >= _prev_e60 and e20 < e60: sig_type = "EMA死叉 空头启动"

    if not sig_type and rs < 30: sig_type = "RSI超卖(%.1f) 超跌反弹" % rs
    elif not sig_type and rs > 70: sig_type = "RSI超买(%.1f) 超涨回调" % rs

    if not sig_type and vr >= 2: sig_type = "倍量(%.1fx) 主力异动" % vr

    _prev_e20,_prev_e60,_prev_c,_prev_r = e20,e60,c,rs

    if sig_type:
        now = time.time()
        if sig_type[:8] not in _cooldown or (now-_cooldown[sig_type[:8]]) > 300:
            _cooldown[sig_type[:8]] = now
            msg = "15M " + sig_type + " $" + "%.2f" % c
            log("SIG: " + msg[:40])
            # Get liquidation data
            liq = scan_liquidation()
            liq_text = ""
            if "error" not in liq:
                tl = liq["total_long"]; ts = liq["total_short"]
                ratio = max(tl, ts) / min(tl, ts) if min(tl, ts) > 0 else 99
                liq_text = " 清算数据：多$%.1fM 空$%.1fM 比1:%.1f" % (tl/1e6, ts/1e6, ratio)
                if liq["short_clusters"]:
                    liq_text += " 空头密集$%d($%.0f万)" % (liq["short_clusters"][0]["price"], liq["short_clusters"][0]["amount"]/10000)
            # AI analysis with liquidation context
            prompt = "技术信号：" + msg + liq_text + "。用六层体系快速分析，先说结论，一屏内说清。"
            analysis = ask_agent(prompt)
            tg(" " + msg + "\n" + analysis)
            log("AI: " + analysis[:40])
            return True
    return False

if __name__ == "__main__":
    log("Scanner+AI started")
    while True:
        try:
            scan()
        except:
            pass
        time.sleep(60)
