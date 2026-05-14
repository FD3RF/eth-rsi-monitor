#!/usr/bin/env python3
"""旺财 - 精英狙击手v4 有regime自知"""
import requests,time,json,os
TG="8640664409:AAHPZIZd1YGa6jCwXziM01qoJ0RJwCfG-LM";TC="8410098965"
DK="sk-6c0916d82cb84a57b5b76fe5b0107cfd"
DU="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
BK="gbRTde9uu3C8AwZBqorEj8"
GA="https://api.gateio.ws/api/v4/futures/usdt"
MF="/root/wangcai_memory.json";CD=300;SL={};ST_cur="IDLE"
L=0;M=[];X={};C={};ST={"t":0,"w":0,"l":0,"p":0,"s":0};RT={}

SYSTEM="""你是旺财，15年职业交易员。你的交易哲学已刻在DNA里：

趋势规则：
- 1H EMA144/169定方向，15M找入场。顺大势逆小势。
- TF冲突时永远等一致，不做对抗单。趋势是你的朋友。

RSI规则：
- RSI<30或>70是衰竭不是反转信号。衰竭区是减仓/观望区，不是开仓区。
- RSI可以持续钝化。抄底摸顶是散户行为，不是你。

清算规则：
- 密集区是利润目标不是到达保证。价格碰到密集区=阻力验证。
- 清算踩踏需要放量确认。

量能规则：
- 无量反弹是假的，倍量突破是真的，缩量回调是健康的。
- 没有成交量确认的信号都是噪音。

风控铁律（打破就要挨打）：
- 每单必须知道在哪个价格认错，不知道就不做。
- 盈亏比<1:2不开。看不懂=不做。
- 市场连续打脸时停下来。最好的交易是你没做的那个。

输出格式（严格执行，以下格式之外的话禁止输出）：
第一行：[✅做多/❌做空/🚫观望] @价格→目标 | 核心逻辑(≤15字)
第二行：可执行指令一句，含入场条件/止损/目标"""

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}",flush=True)

def push(t,b):
    for _ in range(2):
        try:
            requests.post(f"https://api.telegram.org/bot{TG}/sendMessage",json={"chat_id":TC,"text":t[:2000]},timeout=10)
            requests.post("https://api.day.app/push",json={"device_key":BK,"title":t[:50],"body":b[:200],"group":"旺财","level":"timeSensitive","badge":1},timeout=8)
            return
        except: time.sleep(0.5)

def kl(iv,lim):
    try:
        r=requests.get(f"{GA}/candlesticks",params={"contract":"ETH_USDT","interval":iv,"limit":lim},timeout=8)
        if r.status_code!=200: return []
        d=r.json();d.reverse()
        return [{"c":float(x["c"]),"h":float(x["h"]),"l":float(x["l"]),"v":float(x["v"])} for x in d]
    except: return []

def ema(d,p):
    if len(d)<p: return None
    k=2/(p+1);r=sum(x["c"] for x in d[:p])/p
    for x in d[p:]: r=x["c"]*k+r*(1-k)
    return r

def rsi(d,p=14):
    if len(d)<p+1: return 50
    g=l=0
    for i in range(1,p+1):
        z=d[i]["c"]-d[i-1]["c"]
        if z>0: g+=z
        else: l-=z
    return 100-100/(1+(g/p)/(l/p)) if l>0 else 100

def sma(d,p): return sum(x["v"] for x in d[-p:])/p

def bbw(d,p=20,m=2):
    if len(d)<p: return 0
    s=[x["c"] for x in d[-p:]]
    ma=sum(s)/p;v=sum((x-ma)**2 for x in s)/p;std=v**0.5
    return (ma+m*std-(ma-m*std))/ma*100 if ma>0 else 0

def price():
    try:
        r=requests.get(f"{GA}/tickers?contract=ETH_USDT",timeout=8)
        if r.status_code==200:
            d=r.json()[0];return float(d["last"]),float(d["volume_24h"]),float(d["low_24h"]),float(d["high_24h"])
    except: return 0,0,0,0

def liq():
    try:
        r=requests.get("https://www.okx.com/api/v5/public/liquidation-orders",
            params={"instType":"SWAP","instFamily":"ETH-USDT","state":"filled","limit":"50"},timeout=15,headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code!=200: return {}
        data=r.json().get("data",[]);lo={};so={};tl=0;ts=0
        for item in data:
            for d in item.get("details",[]):
                px=round(float(d["bkPx"])/5)*5;usd=float(d["sz"])*px
                if d["posSide"]=="long": lo[px]=lo.get(px,0)+usd;tl+=usd
                else: so[px]=so.get(px,0)+usd;ts+=usd
        return {"tl":round(tl),"ts":round(ts),
                "lc":[{"p":p,"a":round(a)} for p,a in sorted(lo.items(),key=lambda x:-x[1])[:3]],
                "sc":[{"p":p,"a":round(a)} for p,a in sorted(so.items(),key=lambda x:-x[1])[:3]]}
    except: return {}

def oi():
    try:
        r=requests.get("https://www.okx.com/api/v5/public/open-interest",params={"instType":"SWAP","instFamily":"ETH-USDT"},timeout=8)
        if r.status_code==200:
            d=r.json()["data"][0]
            return f"${round(float(d['oiUsd'])/1e6)}M"

    except: return "-"

def oiv():
    try:
        r=requests.get("https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume",params={"ccy":"ETH","period":"1H"},timeout=8)
        if r.status_code==200:
            d=r.json().get("data",[])
            if d: oi=round(float(d[0][1])/1e6);vol=round(float(d[0][2])/1e6);return f"M/M"
    except: pass
    return "-/- "

def bls():
    try:
        r=requests.get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio",params={"symbol":"ETHUSDT","period":"1h","limit":1},timeout=8)
        g=round(float(r.json()[0]["longShortRatio"]),2) if r.status_code==200 else "-"
        r2=requests.get("https://fapi.binance.com/futures/data/topLongShortPositionRatio",params={"symbol":"ETHUSDT","period":"1h","limit":1},timeout=8)
        t=round(float(r2.json()[0]["longShortRatio"]),2) if r2.status_code==200 else "-"
        r3=requests.get("https://fapi.binance.com/futures/data/takerlongshortRatio",params={"symbol":"ETHUSDT","period":"1h","limit":1},timeout=8)
        tk=round(float(r3.json()[0]["buySellRatio"]),2) if r3.status_code==200 else "-"
        r4=requests.get("https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio",params={"ccy":"ETH","period":"1H"},timeout=8)
        ok=round(float(r4.json()["data"][0][1]),2) if r4.status_code==200 and r4.json().get("data") else "-"
        return f"币安{g}/大户{t}/吃单{tk} OKX{ok}"
    except: return "-/-"

def regime():
    m15=kl("15m",20)
    if len(m15)<20: return "UNKNOWN"
    bw=bbw(m15);rs=rsi(m15,14);p=m15[-1]["c"]
    h1=kl("1h",200)
    trend="⚪"
    if len(h1)>=170:
        e144=ema(h1,144);e169=ema(h1,169)
        if e144 and e169: trend="🟢" if e144>e169 else "🔴"
        h4r=kl("4h",200)
    if len(h4r)>=170:
        h4e144=ema(h4r,144);h4e169=ema(h4r,169)
        h4t="🟢" if h4e144 and h4e169 and h4e144>h4e169 else "🔴"
    else: h4t="⚪"
    if bw>8 and (rs<25 or rs>75): return "HIGH_VOL"
    if bw<4: return "RANGE"
    return f"TREND_BULL-{h4t}" if trend=="🟢" else f"TREND_BEAR-{h4t}" if trend=="🔴" else f"RANGE-{h4t}"

def verify():
    global ST,RT
    for x in M:
        if x.get("d") and not x.get("r") and time.time()-x["t"]>7200:
            p=price()[0]
            if x["d"]=="LONG": x["r"]="W" if p>x["p"] else "L"
            elif x["d"]=="SHORT": x["r"]="W" if p<x["p"] else "L"
            if x.get("r"):
                ST[x["r"]]=ST.get(x["r"],0)+1;ST["t"]+=1
                rg=x.get("rg","UNKNOWN");dr=x["d"]
                if rg not in RT: RT[rg]={}
                if dr not in RT[rg]: RT[rg][dr]={"t":0,"w":0,"l":0}
                RT[rg][dr]["t"]+=1;RT[rg][dr][x["r"]]+=1
    ST["p"]=round(ST["w"]/max(ST["t"],1)*100,1)
    ST["s"]=0
    for x in reversed(M):
        if x.get("r")=="W": ST["s"]+=1
        elif x.get("r")=="L": break
    with open(MF,"w") as f: json.dump({"M":M,"ST":ST,"RT":RT},f,ensure_ascii=False)

def scan():
    m15=kl("15m",30);h1=kl("1h",200)
    if len(m15)<25: return None
    c=m15[-1]["c"];v=m15[-1]["v"];vs=sma(m15,20)
    e20=ema(m15,20);e60=ema(m15,60);rs=rsi(m15,14);vr=v/vs if vs>0 else 0
    h1_t="⚪";h4_t="⚪"
    if len(h1)>=170:
        h1e=ema(h1,144);h1el=ema(h1,169)
        h1_t="🟢" if h1e and h1el and h1e>h1el else "🔴"
    h4=kl("4h",200)
    if len(h4)>=170:
        h4e=ema(h4,144);h4el=ema(h4,169)
        h4_t="🟢" if h4e and h4el and h4e>h4el else "🔴"
    sig=""
    if rs<30: sig=f"RSI超卖({rs:.0f})"
    elif rs>70: sig=f"RSI超买({rs:.0f})"
    if not sig and vr>=2: sig=f"倍量({vr:.1f}x)"
    if not sig and e20 and e60:
        pm=kl("15m",31)
        if len(pm)>=30:
            pe20=ema(pm[:-1],20);pe60=ema(pm[:-1],60)
            if pe20 and pe60:
                if pe20<=pe60 and e20>e60: sig="EMA金叉"
                elif pe20>=pe60 and e20<e60: sig="EMA死叉"
    if not sig: return None
    sk=sig[:4]
    if sk in C and time.time()-C[sk]<CD: return None
    C[sk]=time.time()
    return {"s":sig,"p":c,"r":rs,"v":vr,"e20":e20,"e60":e60,"h":h1_t,"h4":h4_t}


def structure(d=None):
    try:
        if d is None: d=kl("15m",30)
        if len(d)<25: return ""
        c=d[-1]["c"];h=d[-1]["h"];l=d[-1]["l"];ph=d[-2]["h"];pl=d[-2]["l"]
        hh=[x["h"] for x in d];ll=[x["l"] for x in d]
        sh=[];sl=[]
        for i in range(1,len(d)-1):
            if hh[i]>hh[i-1] and hh[i]>hh[i+1]: sh.append({"i":i,"p":hh[i]})
            if ll[i]<ll[i-1] and ll[i]<ll[i+1]: sl.append({"i":i,"p":ll[i]})
        sh=sh[-5:];sl=sl[-5:]
        if len(sh)<2 or len(sl)<2: return ""
        rg=max(x["h"] for x in d[-10:])-min(x["l"] for x in d[-10:])
        if rg==0: return ""
        r=[]
        if len(sh)>=3 and len(sl)>=3:
            sp=[s["p"] for s in sh[-3:]];lp=[s["p"] for s in sl[-3:]]
            if all(sp[i]>sp[i+1] for i in range(2)) and all(lp[i]<lp[i+1] for i in range(2)):
                r.append("压缩")
        if len(sh)>=2:
            t1,t2=sh[-2]["p"],sh[-1]["p"];ml=min(x["l"] for x in d[sh[-2]["i"]:sh[-1]["i"]+1])
            if abs(t1-t2)/max(t1,t2)<0.008 and (max(t1,t2)-ml)/max(t1,t2)>0.008: r.append("双顶")
        if len(sl)>=2:
            b1,b2=sl[-2]["p"],sl[-1]["p"];mh=max(x["h"] for x in d[sl[-2]["i"]:sl[-1]["i"]+1])
            if abs(b1-b2)/max(b1,b2)<0.008 and (mh-min(b1,b2))/min(b1,b2)>0.008: r.append("双底")
        tw=h-max(c,ph);bw=min(c,pl)-l
        if tw>rg*0.3 and c<ph: r.append("扫空")
        if bw>rg*0.3 and c>pl: r.append("扫多")
        rh=max(x["h"] for x in d[-5:-1]);rl=min(x["l"] for x in d[-5:-1])
        if h>rh and c<rh: r.append("假突多")
        if l<rl and c>rl: r.append("假突空")
        return "/".join(r) if r else "无"
    except: return "-"

def aggregate(ss,liq,rg,ls,frt):
    """Collect all signals, return market state"""
    if ss is None: return "IDLE"
    p=ss["p"];rs=ss["r"];vr=ss["v"];sig=ss["s"];h4=ss.get("h4","⚪");h1=ss.get("h","⚪")
    st=structure()
    # Direction bias from L/S ratio + regime + structure
    if ls and len(ls)>0:
        try: bn_ls=float(ls.split("/")[0].replace("币安","").strip())
        except: bn_ls=2.0
    else: bn_ls=2.0
    # Regime context
    bear_trend="BEAR" in rg or "🔴" in h4
    bull_trend="BULL" in rg or "🟢" in h4
    # RISK: extreme conditions
    if rs>85 or rs<15 or (vr>3 and ("假突"in st or "扫"in st)):
        return "RISK"
    # READY conditions (confirmed setups with trend alignment)
    # Short: bear trend + overbought + structure confirms
    if bear_trend and rs>70 and ("双顶"in st or "扫空"in st or "假突多"in st):
        return "READY_SHORT"
    # Long: bull trend + oversold + structure confirms
    if bull_trend and rs<30 and ("双底"in st or "扫多"in st or "假突空"in st):
        return "READY_LONG"
    # Short: bear trend + overbought + volume spike
    if bear_trend and rs>70 and vr>2:
        return "READY_SHORT"
    # Long: bull trend + oversold + volume spike
    if bull_trend and rs<30 and vr>2:
        return "READY_LONG"
    # Non-trend READY with extreme L/S + structure
    if rs>75 and bn_ls>2.5 and ("双顶"in st or "假突多"in st):
        return "READY_SHORT"
    if rs<25 and bn_ls<1.5 and ("双底"in st or "假突空"in st):
        return "READY_LONG"
    # WATCH: signals without full confirmation
    if bear_trend and rs>70: return "WATCH_SHORT"
    if bull_trend and rs<30: return "WATCH_LONG"
    if rs>70 or rs<30: return "WATCH_SHORT" if rs>70 else "WATCH_LONG"
    if vr>2: return "WATCH_SHORT" if not bull_trend else "WATCH_LONG"
    if st and st!="无" and st!="-": return "WATCH_SHORT" if bear_trend else "WATCH_LONG"
    return "IDLE"

def think(prompt):
    msgs=[{"role":"system","content":SYSTEM},{"role":"user","content":prompt}]
    for a in range(3):
        try:
            r=requests.post(DU,json={"model":"deepseek-v4-pro","messages":msgs,"max_tokens":300},
                headers={"Authorization":f"Bearer {DK}"},timeout=60)
            if r.status_code!=200: continue
            msg=r.json()["choices"][0]["message"]
            if msg.get("tool_calls"):
                msgs.append(msg)
                for tc in msg["tool_calls"]:
                    fn=tc["function"]["name"]
                    if fn=="price": p,_,_,_=price();result={"p":p}
                    elif fn=="liq": result=liq()
                    else: result={}
                    msgs.append({"role":"tool","tool_call_id":tc["id"],"content":json.dumps(result,ensure_ascii=False)})
                r2=requests.post(DU,json={"model":"deepseek-v4-pro","messages":msgs,"max_tokens":300},
                    headers={"Authorization":f"Bearer {DK}"},timeout=60)
                if r2.status_code==200: return r2.json()["choices"][0]["message"].get("content","")
            else: return msg.get("content","")
        except Exception as e:
            log(f"AI:{e}")
            if a<1: time.sleep(1)
    return "AI不通"

def handle():
    global L
    try:
        r=requests.get(f"https://api.telegram.org/bot{TG}/getUpdates",params={"offset":L+1,"timeout":10},timeout=15)
        if r.status_code!=200: return
        for upd in r.json().get("result",[]):
            L=max(L,upd["update_id"])
            msg=upd.get("message",{});cid=msg.get("chat",{}).get("id");txt=msg.get("text","").strip()
            if not txt or str(cid)!=TC: continue
            log(f"cmd:{txt[:30]}")
            if txt in ("/save","记录复盘"):
                with open("/root/trading_log.md","a",encoding="utf-8") as f:
                    f.write(f"\n## {time.strftime('%m-%d %H:%M')} ${X.get('p',0):.0f}\n{X.get('a','')}\n")
                push("✅复盘已存档","");continue
            if txt=="/start": push("旺财\n/eth /liq /signal /stats /regime /save\n直接聊=AI分析","")
            elif txt=="/eth":
                p,v,lo,hi=price()
                push(f"📊ETH ${p:.0f} 24h${hi:.0f}~${lo:.0f}","") if p else push("失败","")
            elif txt=="/liq":
                d=liq()
                if not d: push("无清算数据","")
                else:
                    rr=max(d["tl"],d["ts"])/max(min(d["tl"],d["ts"]),1)
                    m=f"💀清算 多${d['tl']/1e6:.0f}M/空${d['ts']/1e6:.0f}M 比1:{rr:.0f}"
                    if d.get("sc"): m+=f"\n空密${d['sc'][0]['p']}(${d['sc'][0]['a']/1e4:.0f}万)"
                    if d.get("lc"): m+=f"\n多密${d['lc'][0]['p']}(${d['lc'][0]['a']/1e4:.0f}万)"
                    push(m,"");X["p"]=price()[0]
            elif txt=="/signal":
                s=scan()
                st="无信号" if not s else f'{s["s"]} ${s["p"]:.0f}'
                push(f"📡{st}","")
            elif txt=="/stats":
                rg_txt=""
                for rg,dr in sorted(RT.items()):
                    for d,st_ in sorted(dr.items()):
                        rp=round(st_["w"]/max(st_["t"],1)*100,1)
                        rg_txt+=f"\n{rg}/{d}: {st_['t']}单 胜率{rp}%"
                push(f"📊旺财成绩\n总{ST['t']} 胜{ST['w']} 负{ST['l']} 率{ST['p']}%/{ST['s']}连胜{rg_txt}","")
            elif txt=="/regime":
                rg=regime()
                ex=""
                if rg in RT:
                    for d,s_ in RT[rg].items():
                        if s_["t"]>=3:
                            rp=round(s_["w"]/s_["t"]*100,1)
                            ex+=f" {d}:{s_['t']}单{rp}%"
                push(f"📌当前:{rg}{ex}","")
            else:
                p,_,_,_=price()
                r=think(f"用户问：{txt}\nETH${p:.0f}")
                push("🤖 "+r,"");X["p"]=p;X["a"]=r
    except Exception as e: log(f"tg:{e}")

def main():
    global M,ST,RT
    try:
        with open(MF) as f: d=json.load(f);M=d.get("M",[]);ST=d.get("ST",ST);RT=d.get("RT",{})
    except: M=[]
    for x in M:
        if x.get("r")=="W": ST["w"]+=1;ST["t"]+=1
        elif x.get("r")=="L": ST["l"]+=1;ST["t"]+=1
    ST["p"]=round(ST["w"]/max(ST["t"],1)*100,1)
    log(f"旺财上线 历史{ST['t']}单 胜率{ST['p']}% regime数{len(RT)}")
    if not os.path.exists("/root/trading_log.md"):
        with open("/root/trading_log.md","w") as f: f.write("# 复盘\n")
    ls=0
    while True:
        try: handle()
        except: pass
        t=time.time()
        if t-ls>=60:
            ls=t
            try:
                verify()
                s=scan()
                if s:
                    d=liq()
                    lt=f"多${d['tl']/1e6:.0f}M/空${d['ts']/1e6:.0f}M" if d.get("tl") else "无清算"
                    rg=regime()
                    try:
                        fr=requests.get(f"{GA}/funding_rate?contract=ETH_USDT",timeout=8)
                        frt=f"{'正'if float(fr.json()[0]['funding_rate'])>0 else '负'}{abs(float(fr.json()[0]['funding_rate']))*100:.4f}%" if fr.status_code==200 else "未知"
                    except: frt="未知"
                    try:
                        dp=requests.get(f"{GA}/order_book?contract=ETH_USDT&limit=10",timeout=8)
                        if dp.status_code==200:
                            dd=dp.json();bs=sum(float(b[1])*float(b[0]) for b in dd.get("bids",[]));as_=sum(float(a[1])*float(a[0]) for a in dd.get("asks",[]))
                            dpt=f"买${bs/1e6:.2f}M/卖${as_/1e6:.2f}M" if bs+as_>0 else "未知"
                        else: dpt="未知"
                    except: dpt="未知"
                    mt=""
                    if M and t-M[-1]["t"]<3600: mt=f"前({M[-1]['ts']}):{M[-1]['a'][:40]}"
                    rex=""
                    if rg in RT:
                        for dr,st_ in RT[rg].items():
                            if st_["t"]>=3:
                                rp=round(st_["w"]/st_["t"]*100,1)
                                rex+=f" {dr}:{st_['t']}单{rp}%"
                    pm=f"环境:{rg}{rex}\n信号:{s['s']} ${s['p']:.0f} 1H:{s['h']} RSI:{s['r']:.0f} 量:{s['v']:.1f}x\n清算:{lt} OI:{oi()} OI-vol:{oiv()} L/S:{bls()} 费率:{frt} 深度:{dpt} 结构:{structure()}\n{('前:'+mt) if mt else ''}\n自验:总{ST['t']}胜{ST['w']}负{ST['l']}率{ST['p']}%/{ST['s']}连胜\n\n推理后输出狙击手结论"
                        ls_bn="2.0";ls_ok="2.0"
                        try:
                            x=bls().replace("币安","").replace("OKX","")
    parts=x.split("/")
    if len(parts)>=4: ls_bn=parts[0].strip();ls_ok=parts[3].strip()
except: pass
st_now=aggregate(s,lt,rg,f"币安{ls_bn}/OKX{ls_ok}",frt)
                    if a:
                        push(f"🔔{s['s']} ${s['p']:.0f}\n{a}",f"${s['p']:.0f} {a[:40]}")
                        d="SHORT" if "做空" in a or "❌" in a else "LONG" if "做多" in a or "✅" in a else ""
                        M.append({"t":t,"ts":time.strftime('%H:%M'),"p":s['p'],"s":s['s'],"a":a,"d":d,"rg":rg})
                        if len(M)>20: M=M[-20:]
                        with open(MF,"w") as f: json.dump({"M":M,"ST":ST,"RT":RT},f,ensure_ascii=False)
                        log(f"SIG:{s['s']} ${s['p']:.0f} {rg} d={d}")
            except Exception as e: log(f"scan:{e}")
        time.sleep(1)

if __name__=="__main__": main()
