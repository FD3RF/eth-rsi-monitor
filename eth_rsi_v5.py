#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETH RSI监控 V5.0 - RSI + 量价 + BOLL + EMA + 趋势分析
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

    # 计算辅助指标
    ema_s=calc_ema(c,EMA_SHORT); ema_l=calc_ema(c,EMA_LONG) if len(c)>=EMA_LONG else None
    bm,bu,bl=calc_boll(c)
    avg_v=(sum(v[-VOL_LOOKBACK-1:-1])/VOL_LOOKBACK) if len(v)>=VOL_LOOKBACK+2 else 1
    vr=v[-1]/avg_v if avg_v>0 else 1
    pch=(c[-1]-c[-2])/c[-2]*100 if len(c)>=2 else 0

    # 构建趋势分析
    trend=[]
    if ema_s and ema_l:
        if ema_s>ema_l: trend.append("EMA多头(%.0f>%.0f)"%(ema_s,ema_l))
        else: trend.append("EMA空头(%.0f<%.0f)"%(ema_s,ema_l))
        if pr>ema_s and pr>ema_l: trend.append("价格站上EMA")
        elif pr<ema_s and pr<ema_l: trend.append("价格在EMA下方")
        else: trend.append("价格在EMA之间")
    if bm:
        if pr>bu: trend.append("突破布林上轨")
        elif pr<bl: trend.append("跌破布林下轨")
        elif pr>bm: trend.append("布林中轨上方")
        else: trend.append("布林中轨下方")
    if vr>VOL_SURGE: trend.append("单倍量" if vr<2 else "放量(%.1f倍)"%vr)
    elif vr<VOL_SHRINK: trend.append("缩量(%.2f倍)"%vr)
    tline = " | ".join(trend) if trend else ""
    tline2 = tline.replace("\n","")

    # 1. BOLL突破
    if bu and pr>bu:
        k="boll_up_%s"%iv
        if ac.can_push(k):
            title="BOLL上轨突破·关注回踩"
            body="[%s]\n价格=$%.1f > 上轨=$%.1f\nRSI=%.1f/%.1f/%.1f\n趋势: %s\n分析: 价格突破布林上轨,%s短期过热。\n可等回踩中轨($%.0f)确认。\n%dh:%dm"%(name,pr,bu,r[6],r[14],r[21],tline2,"RSI超买区注意" if r[6]>65 else "但",bm,datetime.now().hour,datetime.now().minute)
            if push(title,body): pc+=1
    if bl and pr<bl:
        k="boll_low_%s"%iv
        if ac.can_push(k):
            title="BOLL下轨突破·关注反弹"
            body="[%s]\n价格=$%.1f < 下轨=$%.1f\nRSI=%.1f/%.1f/%.1f\n趋势: %s\n分析: 价格跌破布林下轨,%s短期超卖。\n可等反抽中轨($%.0f)确认。\n%dh:%dm"%(name,pr,bl,r[6],r[14],r[21],tline2,"RSI超卖区注意反弹" if r[6]<35 else "但",bm,datetime.now().hour,datetime.now().minute)
            if push(title,body): pc+=1

    # 2. RSI低位
    for p in RSI_PERIODS:
        if p in r and r[p]<LOW_THRESHOLD:
            k="low_%s_%d"%(iv,p)
            if ac.can_push(k):
                ax="RSI<30超卖" if r[6]<30 else "RSI偏弱"
                if pr<bl: ax+="+布林下轨下方"
                elif ema_s and ema_l and pr<min(ema_s,ema_l): ax+="+EMA下方弱势"
                if vr>VOL_SURGE and pch<0: ax+="+放量下跌确认空头"
                title="ETH RSI低位·关注超卖"
                body="[%s]\nRSI: %.1f/%.1f/%.1f\n价格=$%.1f\n趋势: %s\n分析: %s，当前不宜追空。\n如缩量企稳+BOLL收口可看反弹。\n%dh:%dm"%(name,r[6],r[14],r[21],pr,tline2,ax,datetime.now().hour,datetime.now().minute)
                if push(title,body): pc+=1

    # 3. RSI高位
    for p in RSI_PERIODS:
        if p in r and r[p]>HIGH_THRESHOLD:
            k="high_%s_%d"%(iv,p)
            if ac.can_push(k):
                ax="RSI>70超买" if r[6]>70 else "RSI偏强"
                if pr>bu: ax+="+布林上轨上方"
                elif ema_s and ema_l and pr>max(ema_s,ema_l): ax+="+EMA上方强势"
                if vr<VOL_SHRINK: ax+="+缩量上涨注意背离"
                title="ETH RSI高位·关注超买"
                body="[%s]\nRSI: %.1f/%.1f/%.1f\n价格=$%.1f\n趋势: %s\n分析: %s，短期过热不宜追多。\n如放量滞涨+长上影可考虑短线。\n%dh:%dm"%(name,r[6],r[14],r[21],pr,tline2,ax,datetime.now().hour,datetime.now().minute)
                if push(title,body): pc+=1

    # 4. 量价
    if v and len(v)>=22:
        # 放量下跌
        if vr>VOL_SURGE and pch<-PRICE_CHG_MIN:
            k="vdrop_%s"%iv
            if ac.can_push(k):
                title="放量下跌"
                body="[%s]\n价格=$%.1f (%.2f%%)\n量比=%.1f倍\n趋势: %s\n分析: 放量杀跌%s，先观望。\n支撑位看BOLL下轨($%.0f)。\n%dh:%dm"%(name,pr,pch,vr,tline2,"+RSI超卖" if r[6]<30 else "+EMA下方" if ema_s and ema_l and pr<min(ema_s,ema_l) else "",bl if bl else 0,datetime.now().hour,datetime.now().minute)
                if push(title,body): pc+=1
        # 缩量上涨+RS高位
        if vr<VOL_SHRINK and pch>PRICE_CHG_MIN*0.6 and r[6]>65:
            k="vbear_%s"%iv
            if ac.can_push(k):
                title="缩量上涨·注意背离"
                body="[%s]\n价格=$%.1f (%.2f%%)\n量比=%.2f倍\n趋势: %s\n分析: %s+缩量新高。如RSI拐头+放量可做空。\n%dh:%dm"%(name,pr,pch,vr,tline2,"RSI高位",datetime.now().hour,datetime.now().minute)
                if push(title,body): pc+=1
        # 放量不涨
        if vr>VOL_SURGE and abs(pch)<0.2:
            k="vtop_%s"%iv
            if ac.can_push(k):
                title="放量不涨·关注顶部"
                body="[%s]\n价格=$%.1f\n量比=%.1f倍\n趋势: %s\n分析: 放量滞涨，%s。\n如BOLL开口缩小+RSI拐头可看空。\n%dh:%dm"%(name,pr,vr,tline2,"RSI高位" if r[6]>65 else "多空博弈中",datetime.now().hour,datetime.now().minute)
                if push(title,body): pc+=1

    pl="  ".join("RSI(%d)=%.2f"%(p,r.get(p,0)) for p in RSI_PERIODS)
    ex="  |  p:%d"%pc if pc else ""
    print("[%s] %s  |  %s%s"%(datetime.now(),name,pl,ex), flush=True)

def main():
    print("="*60); print("ETH RSI监控 V5.0")
    print("时间: %s"%datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print("合约: %s"%CONTRACT); print("RSI: %s  <%d|>%d"%(RSI_PERIODS,LOW_THRESHOLD,HIGH_THRESHOLD))
    print("BOLL(%d,%.0f)  EMA(%d,%d)"%(BOLL_PERIOD,BOLL_STD,EMA_SHORT,EMA_LONG))
    print("量价+分析"); print("推送: Bark"); print("="*60)
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
