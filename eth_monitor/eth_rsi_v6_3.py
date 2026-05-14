#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETH RSI监控 V6.3 - 终极版
方向标记 + 位置判断 + 胜率参考 + 精准人话
"""
import requests, time, threading
from datetime import datetime

GATEIO_API_URL = "https://api.gateio.ws/api/v4"
CONTRACT = "ETH_USDT"
BARK_KEY = "gbRTde9uu3C8AwZBqorEj8"
BARK_API_URL = "https://api.day.app/push"
RSI_PERIODS = [6, 14, 21]
LOW_THRESHOLD = 30; HIGH_THRESHOLD = 70
INTERVALS = {"15m": "15分钟", "1h": "1小时", "4h": "4小时"}
CHECK_INTERVAL = 60; REQUEST_TIMEOUT = 15
VOL_LOOKBACK = 20; VOL_SURGE = 1.5; VOL_SHRINK = 0.5; PRICE_CHG_MIN = 0.5
BOLL_PERIOD = 20; BOLL_STD = 2
EMA_SHORT = 144; EMA_LONG = 169
POSITION_LOOKBACK = 500  # 位置判断用多少根K线

# MACD参数(6,13,5) 与V7一致
MACD_FAST = 6; MACD_SLOW = 13; MACD_SIGNAL = 5

# 胜率查表 (基于1000根15m回测)
WINRATE = {
    "rsi_low":  {"45min":"59%","90min":"60%","180min":"62%"},
    "rsi_high": {"45min":"55%","90min":"56%","180min":"76%"},
    "boll_up":  {"45min":"56%","90min":"53%","180min":"70%"},
    "boll_low": {"45min":"48%","90min":"53%","180min":"54%"},
    "vol_drop": {"45min":"66%","90min":"66%","180min":"66%"},
}

class CooldownSet:
    def __init__(self, cd=1800):
        self._d={}; self._l=threading.Lock(); self._cd=cd
    def can_push(self, k):
        n=time.time()
        with self._l:
            if n-self._d.get(k,0)<self._cd: return False
            self._d[k]=n
            ex=[x for x,t in self._d.items() if t<n-self._cd*2]
            for x in ex: del self._d[x]
            return True
ac = CooldownSet(1800)

def calc_rsi(p, period):
    if not p or len(p)<period+1: return None
    g,l=[],[]
    for i in range(1,len(p)):
        c=p[i]-p[i-1]
        if c>0: g.append(c); l.append(0.0)
        else: g.append(0.0); l.append(abs(c))
    ag=sum(g[:period])/period; al=sum(l[:period])/period
    for i in range(period,len(g)):
        ag=(ag*(period-1)+g[i])/period; al=(al*(period-1)+l[i])/period
    return 100.0-100.0/(1.0+ag/al) if al>1e-10 else 100.0

def get_data(iv):
    for a in range(3):
        try:
            r=requests.get("%s/futures/usdt/candlesticks"%GATEIO_API_URL,
                params={"contract":CONTRACT,"interval":iv,"limit":100},timeout=REQUEST_TIMEOUT)
            r.raise_for_status(); d=r.json()
            if isinstance(d,list) and len(d)>=30:
                return [float(c['c']) for c in d], [float(c['v']) for c in d]
        except:
            if a<2: time.sleep(2**a)
    return None,None

def calc_ema(p, period):
    if len(p)<period: return None
    m=2/(period+1); r=sum(p[:period])/period
    for x in p[period:]: r=(x-r)*m+r
    return r

def calc_boll(p, period=BOLL_PERIOD, std=BOLL_STD):
    if len(p)<period: return None,None,None
    s=sum(p[-period:])/period; v=sum((x-s)**2 for x in p[-period:])/period; sd=v**0.5
    return s, s+std*sd, s-std*sd

def calc_macd(closes, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
    """MACD计算，返回(macd值, 信号线, 金叉/死叉标签, 零轴位置)"""
    if len(closes) < slow + signal + 2:
        return None, None, "", ""
    def ema_series(data, period):
        if len(data) < period: return None
        k = 2/(period+1); ema = sum(data[:period])/period
        r = [ema]
        for x in data[period:]: ema = (x-ema)*k+ema; r.append(ema)
        return r
    ema12 = ema_series(closes, fast)
    ema26 = ema_series(closes, slow)
    if not ema12 or not ema26: return None, None, "", ""
    offset = len(ema12) - len(ema26)
    ema12 = ema12[offset:]
    macd_line = [ema12[i]-ema26[i] for i in range(len(ema12))]
    sig_line = ema_series(macd_line, signal)
    if not sig_line or len(sig_line) < 2: return None, None, "", ""
    cm, cs = macd_line[-1], sig_line[-1]
    pm, ps = macd_line[-2], sig_line[-2]
    tag = ""
    if pm <= ps and cm > cs:
        tag = "MACD金叉·零轴上" if cm > 0 else "MACD金叉·零轴下"
    elif pm >= ps and cm < cs:
        tag = "MACD死叉·零轴上" if cm > 0 else "MACD死叉·零轴下"
    return macd_line[-1], sig_line[-1], tag, "零轴上" if cm > 0 else "零轴下"

def detect_position(pr, c):
    """判断价格在近期区间的位置：高位区/中位区/低位区"""
    lookback = min(POSITION_LOOKBACK, len(c))
    if lookback < 20:
        return ""
    recent = c[-lookback:]
    lo = min(recent)
    hi = max(recent)
    rng = hi - lo
    if rng < 1:
        return ""
    pct = (pr - lo) / rng
    if pct > 0.75:
        return "高位区"
    elif pct < 0.25:
        return "低位区"
    else:
        return "中位区"

def push(title, body):
    for a in range(3):
        try:
            r=requests.post(BARK_API_URL, json={
                "device_key":BARK_KEY,"title":title,"body":body,
                "level":"timeSensitive","badge":1,
                "group":"ETH-RSI-V63"}, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            if r.json().get("code")==200: print("[%s] push ok" % datetime.now()); return True
        except:
            if a<2: time.sleep(1)
    return False

def run_period(iv, name):
    c,v=get_data(iv)
    if not c or len(c)<22: return
    r={}
    for p in RSI_PERIODS:
        x=calc_rsi(c,p)
        if x is not None: r[p]=x
    if len(r)<3: return
    pr=c[-1]; pc=0

    ema_s=calc_ema(c,EMA_SHORT); ema_l=calc_ema(c,EMA_LONG) if len(c)>=EMA_LONG else None
    bm,bu,bl=calc_boll(c)
    avg_v=(sum(v[-VOL_LOOKBACK-1:-1])/VOL_LOOKBACK) if len(v)>=VOL_LOOKBACK+2 else 1
    vr=v[-1]/avg_v if avg_v>0 else 1
    pch=(c[-1]-c[-2])/c[-2]*100 if len(c)>=2 else 0
    pos = detect_position(pr, c)
    
    # MACD
    _, _, macd_tag, _ = calc_macd(c)
    macd_line = "  %s" % macd_tag if macd_tag else ""

    now_str = "%dh:%dm"%(datetime.now().hour,datetime.now().minute)
    hr = "[%s] $%.0f"%(name,pr)

    pushed = False  # 每周期最多推 1 条

    # ========== 1. BOLL上轨突破 ==========
    if bu and pr>bu and not pushed:
        k="boll_up_%s"%iv
        if ac.can_push(k):
            wr = WINRATE["boll_up"].get("180min","")
            wr_s = " 近3h下跌概率%s" % wr if wr else ""
            title="⚠️🔴 涨太快了别追·等待做空"
            body=("%s\n现价$%.0f 冲出布林带上轨 %s%s%s\n"
                  "👉 等回踩$%.0f缩量企稳再考虑，追高9成站岗。\n%s")%(hr,pr,pos,wr_s,macd_line,bm,now_str)
            if push(title,body): pc+=1; pushed=True

    # ========== 2. BOLL下轨突破 ==========
    if bl and pr<bl and not pushed:
        k="boll_low_%s"%iv
        if ac.can_push(k):
            wr = WINRATE["boll_low"].get("180min","")
            wr_s = " 近3h反弹概率%s" % wr if wr else ""
            title="⚠️🟢 跌太狠了别慌·等待做多"
            body=("%s\n现价$%.0f 跌破布林带下轨 %s%s%s\n"
                  "👉 别割肉，等回到$%.0f+出阳线止跌再说。\n%s")%(hr,pr,pos,wr_s,macd_line,bm,now_str)
            if push(title,body): pc+=1; pushed=True

    # ========== 3. RSI低位 ==========
    for p in RSI_PERIODS:
        if p in r and r[p]<LOW_THRESHOLD and not pushed:
            k="low_%s_%d"%(iv,p)
            if ac.can_push(k):
                extra=""
                if pr<bl: extra+=" 跌出布林带下轨"
                elif ema_s and ema_l and pr<min(ema_s,ema_l): extra+=" 大方向空头"
                if vr>VOL_SURGE and pch<0: extra+=" 放量砸盘中"
                wr = WINRATE["rsi_low"].get("180min","")
                wr_s = " 近3h反弹概率%s" % wr if wr else ""
                title="⚠️🟢 短线跌过头了·关注做多"
                body=("%s\nRSI=%.0f 超卖 %s%s%s%s\n"
                      "👉 RSI回到35以上+阳线再考虑多。\n%s")%(hr,r[p],pos,wr_s,extra,macd_line,now_str)
                if push(title,body): pc+=1; pushed=True

    # ========== 4. RSI高位 ==========
    for p in RSI_PERIODS:
        if p in r and r[p]>HIGH_THRESHOLD and not pushed:
            k="high_%s_%d"%(iv,p)
            if ac.can_push(k):
                extra=""
                if pr>bu: extra+=" 冲出布林带上轨"
                elif ema_s and ema_l and pr>max(ema_s,ema_l): extra+=" 大方向多头"
                if vr<VOL_SHRINK: extra+=" 缩量拉涨小心假突破"
                wr = WINRATE["rsi_high"].get("180min","")
                wr_s = " 近3h下跌概率%s" % wr if wr else ""
                title="⚠️🔴 短线涨过头了·关注做空"
                body=("%s\nRSI=%.0f 超买 %s%s%s%s\n"
                      "👉 RSI回到60以下+缩量回调再考虑空。\n%s")%(hr,r[p],pos,wr_s,extra,macd_line,now_str)
                if push(title,body): pc+=1; pushed=True

    # ========== 5. 放量下跌 ==========
    if v and len(v)>=22 and not pushed:
        if vr>VOL_SURGE and pch<-PRICE_CHG_MIN:
            k="vdrop_%s"%iv
            if ac.can_push(k):
                extra=""
                if r[6]<30: extra=" RSI超卖但放量砸盘别急着抄"
                wr = WINRATE["vol_drop"].get("180min","")
                wr_s = " 近3h反弹概率%s" % wr if wr else ""
                title="🔴 有人在砸盘·别接飞刀"
                body=("%s\n放量暴跌%.2f%% 量能%.1f倍 %s%s%s\n"
                      "👉 缩量企稳+阳线反包再考虑。\n%s")%(hr,pch,vr,pos,wr_s,macd_line,now_str)
                if push(title,body): pc+=1; pushed=True

        # ========== 6. 缩量上涨背离 ==========
        if vr<VOL_SHRINK and pch>PRICE_CHG_MIN*0.6 and r[6]>65 and not pushed:
            k="vbear_%s"%iv
            if ac.can_push(k):
                title="🐻🔴 涨不动了·关注做空"
                body=("%s\n涨了但量仅%.2f倍 %s 典型量价背离%s\n"
                      "👉 RSI拐头向下+突然放量=做空信号。\n%s")%(hr,vr,pos,macd_line,now_str)
                if push(title,body): pc+=1; pushed=True

        # ========== 7. 放量不涨 ==========
        if vr>VOL_SURGE and abs(pch)<0.2 and not pushed:
            k="vtop_%s"%iv
            if ac.can_push(k):
                more=" RSI高位" if r[6]>65 else " 方向不明"
                title="⚖️ 多空在打架·先观望"
                body=("%s%s %s 放量滞涨%s\n"
                      "👉 谁赢跟谁走，别冲进去当炮灰。\n%s")%(hr,more,pos,macd_line,now_str)
                if push(title,body): pc+=1; pushed=True

    pl="  ".join("RSI(%d)=%.2f"%(p,r.get(p,0)) for p in RSI_PERIODS)
    ex="  |  p:%d"%pc if pc else ""
    print("[%s] %s  |  %s%s"%(datetime.now(),name,pl,ex), flush=True)

def main():
    print("="*60); print("ETH RSI监控 V6.3 - 终极版")
    print("时间: %s"%datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print("合约: %s"%CONTRACT); print("RSI: %s  <%d|>%d"%(RSI_PERIODS,LOW_THRESHOLD,HIGH_THRESHOLD))
    print("BOLL(%d,%.0f)  EMA(%d,%d)"%(BOLL_PERIOD,BOLL_STD,EMA_SHORT,EMA_LONG))
    print("量价+分析 位置判断 胜率参考"); print("推送: Bark (终极版)"); print("="*60)
    while True:
        try:
            for iv,nm in INTERVALS.items():
                try: run_period(iv,nm)
                except Exception as e: print("[%s] %s e: %s"%(datetime.now(),nm,e))
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt: break
        except Exception as e:
            print("[%s] main e: %s"%(datetime.now(),e)); time.sleep(CHECK_INTERVAL)

if __name__=="__main__": main()
