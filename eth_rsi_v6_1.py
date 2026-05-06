#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETH RSI监控 V6.1 - 精准人话版
根据七层口诀优化推送文案，指导性更强
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

def push(title, body):
    for a in range(3):
        try:
            r=requests.post(BARK_API_URL, json={
                "device_key":BARK_KEY,"title":title,"body":body,
                "level":"timeSensitive","badge":1}, timeout=REQUEST_TIMEOUT)
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

    now_str = "%dh:%dm"%(datetime.now().hour,datetime.now().minute)
    hr = "[%s] $%.0f"%(name,pr)

    # ========== 1. BOLL上轨突破 ==========
    if bu and pr>bu:
        k="boll_up_%s"%iv
        if ac.can_push(k):
            title="⚠️ 涨太快了别追"
            body=("%s\n现价$%.0f，冲出布林带上轨了，短期过热。\n"
                  "👉 等回踩$%.0f附近缩量企稳再考虑，现在追高9成概率站岗。\n%s")%(hr,pr,bm,now_str)
            if push(title,body): pc+=1

    # ========== 2. BOLL下轨突破 ==========
    if bl and pr<bl:
        k="boll_low_%s"%iv
        if ac.can_push(k):
            title="⚠️ 跌太狠了别慌"
            body=("%s\n现价$%.0f，跌破布林带下轨，属于极端超卖。\n"
                  "👉 别在这割肉，等价格回到$%.0f+出阳线止跌再说。\n%s")%(hr,pr,bm,now_str)
            if push(title,body): pc+=1

    # ========== 3. RSI低位 ==========
    for p in RSI_PERIODS:
        if p in r and r[p]<LOW_THRESHOLD:
            k="low_%s_%d"%(iv,p)
            if ac.can_push(k):
                extra=""
                if pr<bl: extra+=" 而且跌出布林带下轨了"
                elif ema_s and ema_l and pr<min(ema_s,ema_l): extra+=" 大方向还是空头"
                if vr>VOL_SURGE and pch<0: extra+=" 有人在放量砸盘，不一定到底了"
                title="⚠️ 短线跌过头了"
                body=("%s\nRSI到了%.0f，短线超卖严重。\n"
                      "👉 别追空，等RSI回到35以上+出阳线再说。%s\n%s")%(hr,r[p],extra,now_str)
                if push(title,body): pc+=1

    # ========== 4. RSI高位 ==========
    for p in RSI_PERIODS:
        if p in r and r[p]>HIGH_THRESHOLD:
            k="high_%s_%d"%(iv,p)
            if ac.can_push(k):
                extra=""
                if pr>bu: extra+=" 而且冲出布林带上轨了"
                elif ema_s and ema_l and pr>max(ema_s,ema_l): extra+=" 大方向还是多头"
                if vr<VOL_SHRINK: extra+=" 但缩量拉上去的，小心假突破"
                title="⚠️ 短线涨过头了"
                body=("%s\nRSI到了%.0f，短线过热。\n"
                      "👉 别追涨，等RSI回到60以下+缩量回调再考虑。%s\n%s")%(hr,r[p],extra,now_str)
                if push(title,body): pc+=1

    # ========== 5. 放量下跌 ==========
    if v and len(v)>=22:
        if vr>VOL_SURGE and pch<-PRICE_CHG_MIN:
            k="vdrop_%s"%iv
            if ac.can_push(k):
                extra=""
                if r[6]<30: extra=" RSI也超卖了，但放量砸盘不要急着抄底"
                title="🔴 有人在砸盘"
                body=("%s\n放量暴跌%.2f%%，量能是平时的%.1f倍，空头碾压。\n"
                      "👉 别接飞刀，等缩量企稳+阳线反包再说。%s\n%s")%(hr,pch,vr,extra,now_str)
                if push(title,body): pc+=1

        # ========== 6. 缩量上涨背离 ==========
        if vr<VOL_SHRINK and pch>PRICE_CHG_MIN*0.6 and r[6]>65:
            k="vbear_%s"%iv
            if ac.can_push(k):
                title="🐻 涨不动了小心回调"
                body=("%s\n涨了但量只有平时的%.2f倍，典型的量价背离。\n"
                      "👉 盯着RSI，一旦拐头向下+突然放量，就是做空信号。\n%s")%(hr,vr,now_str)
                if push(title,body): pc+=1

        # ========== 7. 放量不涨 ==========
        if vr>VOL_SURGE and abs(pch)<0.2:
            k="vtop_%s"%iv
            if ac.can_push(k):
                more="RSI在高位" if r[6]>65 else "多空还在激烈博弈"
                title="⚖️ 多空在打架"
                body=("%s，%s，成交量放大但价格没动。\n"
                      "👉 谁赢跟谁走，别自己冲进去当炮灰。\n%s")%(hr,more,now_str)
                if push(title,body): pc+=1

    pl="  ".join("RSI(%d)=%.2f"%(p,r.get(p,0)) for p in RSI_PERIODS)
    ex="  |  p:%d"%pc if pc else ""
    print("[%s] %s  |  %s%s"%(datetime.now(),name,pl,ex), flush=True)

def main():
    print("="*60); print("ETH RSI监控 V6.1 - 精准人话版")
    print("时间: %s"%datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print("合约: %s"%CONTRACT); print("RSI: %s  <%d|>%d"%(RSI_PERIODS,LOW_THRESHOLD,HIGH_THRESHOLD))
    print("BOLL(%d,%.0f)  EMA(%d,%d)"%(BOLL_PERIOD,BOLL_STD,EMA_SHORT,EMA_LONG))
    print("量价+分析"); print("推送: Bark (精准人话版)"); print("="*60)
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
