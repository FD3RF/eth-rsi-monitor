#!/usr/bin/env python3
"""旺财 - 狙击手v5 状态机架构"""
import requests,time,json,os
TG="8640664409:AAHPZIZd1YGa6jCwXziM01qoJ0RJwCfG-LM";TC="8410098965"
DK="sk-6c0916d82cb84a57b5b76fe5b0107cfd"
DU="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
BK="gbRTde9uu3C8AwZBqorEj8"
GA="https://api.gateio.ws/api/v4/futures/usdt"
MF="/root/wangcai_memory.json"
L=0;M=[];X={};ST={"t":0,"w":0,"l":0,"p":0,"s":0};RT={};SL={};Sn={};Sc=0
JT_BUY=0;JT_SELL=0;JT_MSG=""  # 九转全局计数

def calc_jiuzhuan(d):
    """计算神奇九转计数，返回当前计数状态"""
    global JT_BUY, JT_SELL, JT_MSG
    if len(d) < 5:
        return ""
    c = d[-1]["c"]
    c4 = d[-5]["c"]  # close[4]
    
    if c < c4:
        JT_SELL = 0
        JT_BUY += 1
        if JT_BUY == 9:
            JT_MSG = "下跌九转9✓"
            return JT_MSG
        else:
            JT_MSG = f"下跌九转{JT_BUY}"
            return JT_MSG
    elif c > c4:
        JT_BUY = 0
        JT_SELL += 1
        if JT_SELL == 9:
            JT_MSG = "上涨九转9✓"
            return JT_MSG
        else:
            JT_MSG = f"上涨九转{JT_SELL}"
            return JT_MSG
    else:
        JT_BUY = 0
        JT_SELL = 0
        JT_MSG = ""
        return ""

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}",flush=True)
def push(t,b):
    for _ in range(2):
        try:
            requests.post(f"https://api.telegram.org/bot{TG}/sendMessage",json={"chat_id":TC,"text":t[:2000]},timeout=10)
            requests.post("https://api.day.app/push",json={"device_key":BK,"title":t[:50],"body":b[:200],"group":"旺财","level":"timeSensitive","badge":1},timeout=8)
            return
        except: time.sleep(0.5)

# ═══ DATA ═══
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
        if z>0: g+=z;l+=0
        else: l-=z;g+=0
    return 100-100/(1+(g/p)/(l/p)) if l>0 else 100

def sma(d,p): return sum(x["v"] for x in d[-p:])/p
def bbw(d,p=20,m=2):
    if len(d)<p: return 0
    s=[x["c"] for x in d[-p:]];ma=sum(s)/p;v=sum((x-ma)**2 for x in s)/p;std=v**0.5
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
            d=r.json()["data"][0];return f"${round(float(d['oiUsd'])/1e6)}M"
    except: return "-"

def oiv():
    try:
        r=requests.get("https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume",params={"ccy":"ETH","period":"1H"},timeout=8)
        if r.status_code==200:
            d=r.json().get("data",[])
            if d: oi=round(float(d[0][1])/1e6);vol=round(float(d[0][2])/1e6);return f"${oi}M/${vol}M"
    except: pass
    return "-/-"

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
    with open(MF,"w") as f: json.dump({"M":M,"ST":ST,"RT":RT,"SL":SL},f,ensure_ascii=False)

def scan():
    global JT_MSG
    m15=kl("15m",30);h1=kl("1h",200);h4=kl("4h",200)
    if len(m15)<25: return None
    c=m15[-1]["c"];v=m15[-1]["v"];vs=sma(m15,20)
    e20=ema(m15,20);e60=ema(m15,60);rs=rsi(m15,14);vr=v/vs if vs>0 else 0;bw=bbw(m15)
    h1_t="⚪";h4_t="⚪"
    if len(h1)>=170:
        h1e=ema(h1,144);h1el=ema(h1,169)
        h1_t="🟢" if h1e and h1el and h1e>h1el else "🔴"
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
    if sk in SL and time.time()-SL[sk]<300: return None
    SL[sk]=time.time()
    # 计算九转
    jt = calc_jiuzhuan(m15)
    return {"s":sig,"p":c,"r":rs,"v":vr,"e20":e20,"e60":e60,"h":h1_t,"h4":h4_t,"bb":bw,"vol":v,"jt":jt}

def structure(d=None):
    try:
        if d is None: d=kl("15m",30)
        if len(d)<25: return ""
        c=d[-1]["c"];h=d[-1]["h"];l=d[-1]["l"];ph=d[-2]["h"];pl=d[-2]["l"]
        hh=[x["h"] for x in d];ll=[x["l"] for x in d];sh=[];sl=[]
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

# ═══ STATE MACHINE ═══
_S = {"env":"IDLE","opp":"IDLE","sys":"NORMAL","str":"","pri":4}

def assess(d,ls_str,frt_str):
    """Three-layer state assessment"""
    if d is None: return "IDLE","IDLE","NORMAL","",4
    p=d["p"];rs=d["r"];vr=d["v"];h4=d["h4"];h1=d["h"];bw=d.get("bb",0);sig=d["s"]
    st=structure();bear4="🔴" in h4;bull4="🟢" in h4;bear1="🔴" in h1;bull1="🟢" in h1
    bear=bear4 or bear1;bull=bull4 or bull1;mix=(bear4 and bull1) or (bull4 and bear1)
    
    # Parse L/S
    bn_ls=2.0;ok_ls=2.0
    try:
        x=ls_str.replace("币安","").replace("OKX","")
        p2=x.split("/")
        if len(p2)>=4: bn_ls=float(p2[0]);ok_ls=float(p2[3])
    except: pass
    ls_extreme=bn_ls>2.5 or bn_ls<1.5
    
    # ── Layer 1: Environment ──
    if bw>8 and (rs>80 or rs<20) and vr>2: env="CHAOS"
    elif bw<4 and vr<1: env="SQUEEZE"
    elif bw>6 and vr>2: env="EXPANSION"
    elif bear and not mix: env="TREND_BEAR"
    elif bull and not mix: env="TREND_BULL"
    else: env="RANGE"
    
    # ── Layer 2: Opportunity ──
    opp="IDLE";strg=""
    conf_bear=bear and (rs>70 or "双顶"in st or "假突多"in st or "扫空"in st)
    conf_bull=bull and (rs<30 or "双底"in st or "假突空"in st or "扫多"in st)
    watch_bear=bear and rs>65 or (rs>70 and bn_ls>2.5)
    watch_bull=bull and rs<35 or (rs<30 and bn_ls<1.5)
    exh_bear=bear and rs<25 and "无"in st
    exh_bull=bull and rs>75 and "无"in st
    
    if vr>3 and rs>75 and bear: opp="REVERSAL_RISK";strg="STRONG"
    elif rs<20 and vr>2 and bull: opp="EXHAUSTION";strg="STRONG"
    elif conf_bear and ls_extreme: opp="SHORT_CONFIRM";strg="STRONG"
    elif conf_bull and ls_extreme: opp="LONG_CONFIRM";strg="STRONG"
    elif conf_bear: opp="SHORT_CONFIRM";strg="MEDIUM"
    elif conf_bull: opp="LONG_CONFIRM";strg="MEDIUM"
    elif watch_bear: opp="WATCH_SHORT";strg="MEDIUM" if rs>70 else "WEAK"
    elif watch_bull: opp="WATCH_LONG";strg="MEDIUM" if rs<30 else "WEAK"
    elif exh_bear: opp="EXHAUSTION";strg="WEAK"
    elif exh_bull: opp="EXHAUSTION";strg="WEAK"
    elif vr>2: opp="WATCH_SHORT" if bear else "WATCH_LONG";strg="WEAK"
    elif st not in ("","无","-"): opp="WATCH_SHORT" if bear else "WATCH_LONG";strg="WEAK"
    
    # ── Layer 3: System ──
    if env=="CHAOS": sys_s="RISK_ONLY"
    elif opp in ("LONG_CONFIRM","SHORT_CONFIRM") and strg=="STRONG": sys_s="HIGH_CONFIDENCE"
    elif opp!="IDLE": sys_s="NORMAL"
    else: sys_s="SILENT"
    
    # Archetype
    a="反弹" if opp=="LONG_CONFIRM" and st and ("假突"in st or "扫"in st) else       "砸盘" if opp=="SHORT_CONFIRM" and st and ("假突"in st or "扫"in st) else       "突破" if opp=="LONG_CONFIRM" and vr>1.5 else       "放空" if opp=="SHORT_CONFIRM" and vr>1.5 else       "顺势" if opp=="LONG_CONFIRM" and bull4 else       "顺势" if opp=="SHORT_CONFIRM" and bear4 else       "反弹" if opp=="LONG_CONFIRM" and env=="RANGE" else       "回调空" if opp=="SHORT_CONFIRM" and env=="RANGE" else       "反转尝试" if opp in ("REVERSAL_RISK","EXHAUSTION") else       "关注" if "WATCH" in opp else "-"
    pri=0 if env=="CHAOS" else 1 if opp=="REVERSAL_RISK" else 2 if "CONFIRM" in opp else 3
    return env,opp,sys_s,strg,pri,a

def think(prompt,t=20):
    msgs=[{"role":"system","content":"你是ETH狙击手旺财。规则:1状态机方向=结论 2格式:结论+证据+风险+动作 3必须给止损 4禁止矛盾句 5风险只列事实 6IDLE状态只输观望/等待不给交易条件"},{"role":"user","content":prompt}]
    for a in range(2):
        try:
            r=requests.post(DU,json={"model":"deepseek-v4-pro","messages":msgs,"max_tokens":300},
                headers={"Authorization":f"Bearer {DK}"},timeout=t)
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
                    headers={"Authorization":f"Bearer {DK}"},timeout=t)
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
                push("复盘已存档","");continue
            if txt=="/start": push("旺财\n/eth /liq /signal /stats /save\n直接聊=AI分析","")
            elif txt=="/eth":
                p,v,lo,hi=price()
                push(f"ETH ${p:.0f} 24h${hi:.0f}~${lo:.0f}","") if p else push("失败","")
            elif txt=="/liq":
                d=liq()
                if not d: push("无清算","")
                else:
                    rr=max(d["tl"],d["ts"])/max(min(d["tl"],d["ts"]),1)
                    m=f"清算 多${d['tl']/1e6:.0f}M/空${d['ts']/1e6:.0f}M 比1:{rr:.0f}"
                    if d.get("sc"): m+=f"\n空密${d['sc'][0]['p']}(${d['sc'][0]['a']/1e4:.0f}万)"
                    if d.get("lc"): m+=f"\n多密${d['lc'][0]['p']}(${d['lc'][0]['a']/1e4:.0f}万)"
                    push(m,"");X["p"]=price()[0]
            elif txt=="/signal":
                s=scan()
                push(f"无信号" if not s else f"{s['s']} ${s['p']:.0f}","")
            elif txt=="/stats":
                push(f"旺财 总{ST['t']} 胜{ST['w']} 负{ST['l']} 率{ST['p']}%/{ST['s']}连胜","")
            elif txt=="/health":
                push(health(),"")
            elif txt.startswith("/trace"):
                try:
                    n=int(txt.split()[1]) if len(txt.split())>1 else 1
                    if n<1 or n>len(M): push("序号超范围","")
                    else:
                        x=M[-n];msg="#"+str(x.get("ts",""))+" $"+str(x.get("p",""))+" 信号:"+str(x.get("s",""))+" 状态:"+str(x.get("rg","-"))+" 方向:"+str(x.get("d","-"));push(msg,"")
                except: push("用法: /trace 编号","")
            else:
                p,_,_,_=price()
                cn_e={"RANGE":"盘整","CHAOS":"混乱","TREND_BEAR":"空头趋势","TREND_BULL":"多头趋势","SQUEEZE":"压缩","EXPANSION":"扩张"}
                cn_o={"IDLE":"无信号","WATCH_LONG":"关注做多","WATCH_SHORT":"关注做空","LONG_CONFIRM":"确认做多","SHORT_CONFIRM":"确认做空","EXHAUSTION":"趋势衰竭","REVERSAL_RISK":"反转风险"}
                e=cn_e.get(_S.get("env",""),_S.get("env",""));o=cn_o.get(_S.get("opp",""),_S.get("opp",""));s=_S.get("str","");st=_S.get("sys","")
                r=think(f"基于当前状态回答用户问题。状态：环境{e} 机会{o}({s}) 系统{st}\n用户问：{txt}\nETH${p:.0f}")
                push(r,"");X["p"]=p;X["a"]=r
    except Exception as e: log(f"tg:{e}")

# ═══ MAIN ═══

def health_log(k,v=1):
    global HL
    if k=="push": HL["push"]+=1
    elif k=="hold": HL["hold"]+=1
    elif k=="err": HL["err"]+=1
    elif k=="block": HL["block"]+=1
    elif k=="st":
        s=v;HL["states"][s]=HL["states"].get(s,0)+1

def health():
    u=(time.time()-HL.get("t0",time.time()))/60
    p=HL["push"];h=HL["hold"];e=HL.get("err",0);b=HL.get("block",0)
    st=sorted(HL["states"].items(),key=lambda x:-x[1])[:3]
    ts=" ".join(f"{s}:{n}" for s,n in st)
    return f"旺财运行{u:.0f}分 推{p}抑{h}错{e}拦{b} 状态{ts}"

def main():
    global M,ST,RT,SL,_S,Sc
    try:
        with open(MF) as f: d=json.load(f);M=d.get("M",[]);ST=d.get("ST",ST);RT=d.get("RT",{});SL=d.get("SL",{})
    except: M=[]
    for x in M:
        if x.get("r")=="W": ST["w"]+=1;ST["t"]+=1
        elif x.get("r")=="L": ST["l"]+=1;ST["t"]+=1
    ST["p"]=round(ST["w"]/max(ST["t"],1)*100,1)
    log(f"旺财上线 历史{ST['t']}单 胜率{ST['p']}% states:{len(SL)}")
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
                    ls_str=bls()
                    env,opp,sys_s,strg,pri,at=assess(s,ls_str,frt)
                    state_id=f"{env}/{opp}/{strg}"
                    now_state={"env":env,"opp":opp,"sys":sys_s,"str":strg,"pri":pri,"id":state_id,"t":t}
                    
                    # State decay: check if current state should degrade
                    if _S["opp"]!="IDLE" and t-SL.get(f"st_{_S['opp']}",0)>3600:
                        _S={"env":"IDLE","opp":"IDLE","sys":"SILENT","str":"","pri":4}
                        log(f"DECAY: {_S['opp']} expired")
                    
                    # State transition check
                    prev_id=_S.get("id","");prev_str=_S.get("str","")
                    should_push=False
                    if opp=="IDLE": should_push=False
                    elif prev_id!=state_id and strg!=prev_str and opp!=_S.get("opp",""):
                        # Cross-state or cross-strength transition (WEAK→MEDIUM, MEDIUM→STRONG)
                        should_push=True
                    elif opp!=_S.get("opp","") and opp!="IDLE":
                        # Different opportunity state
                        should_push=True
                    
                    # Priority gating: P0 (CHAOS) suppresses all trade signals
                    if env=="CHAOS" and opp not in ("REVERSAL_RISK","EXHAUSTION"):
                        should_push=False
                        log(f"STATE:{state_id} (suppressed by CHAOS)")
                    
                    if should_push:
                        _S=now_state;SL[f"st_{opp}"]=t;Sc+=1
                        rg=f"{env}/{opp}"
                        mt=""
                        if M and t-M[-1]["t"]<3600: mt=f"前({M[-1]['ts']}):{M[-1]['a'][:40]}"
                        rex=""
                        pm=f"环境:{rg} 信号:{s['s']} ${s['p']:.0f} 4H:{s['h4']} 1H:{s['h']} RSI:{s['r']:.0f} 量:{s['v']:.1f}x\n清算:{lt} OI:{oi()} OI-vol:{oiv()} L/S:{ls_str} 费率:{frt} 深度:{dpt} 结构:{structure()}\n{('前:'+mt) if mt else ''}\n自验:总{ST['t']}胜{ST['w']}负{ST['l']}率{ST['p']}%/{ST['s']}连胜\n\n推理后输出结论"
                        a=think(pm,25)
                        if a:
                            cn={"LONG_CONFIRM":"确认做多","SHORT_CONFIRM":"确认做空","WATCH_LONG":"关注做多","WATCH_SHORT":"关注做空","REVERSAL_RISK":"反转风险","EXHAUSTION":"趋势衰竭","WEAK":"弱","MEDIUM":"中","STRONG":"强"}
                            c_opp=cn.get(opp,opp);c_str=cn.get(strg,strg)
                            emoji="🔴" if env=="CHAOS" else "🟢" if "CONFIRM" in opp else "🟡"
                            jt_info = f"\n{JT_MSG}" if JT_MSG else ""
                            push(f"{emoji}{at} ${s['p']:.0f}{jt_info}\n{a}",f"${s['p']:.0f} {a[:40]}")
                            d="SHORT" if "做空" in a or "❌" in a else "LONG" if "做多" in a or "✅" in a else ""
                            M.append({"t":t,"ts":time.strftime('%H:%M'),"p":s['p'],"s":s['s'],"a":a,"d":d,"rg":rg,"st":state_id})
                            if len(M)>20: M=M[-20:]
                            with open(MF,"w") as f: json.dump({"M":M,"ST":ST,"RT":RT,"SL":SL},f,ensure_ascii=False)
                            health_log("push");health_log("st",rg);log(f"PUSH:{rg}({strg})@{s['s']} ${s['p']:.0f}")
                    else:
                        health_log("hold");log(f"HOLD:{state_id} (prev:{prev_id})")
            except Exception as e: log(f"scan:{e}")
        time.sleep(1)

if __name__=="__main__": main()
