import requests, time, sys

LOG = r"C:\Users\Administrator\WorkBuddy\Claw\winrate_out.txt"
try:
    with open(LOG,"w") as f: f.write("START\n")
except:
    pass

def log(m):
    try:
        with open(LOG,"a") as f: f.write(str(m)+"\n")
    except:
        pass

try:
    log("=== WINRATE ===")
    API = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
    
    # Fetch 1000 candles 15m (about 10 days)
    log("Fetching 15m data...")
    r = requests.get(API, params={"contract":"ETH_USDT","interval":"15m","limit":1000}, timeout=30)
    d = r.json()
    log(f"Got {len(d)} candles")
    
    closes = [float(x['c']) for x in d][::-1]  # oldest first
    volumes = [float(x['v']) for x in d][::-1]
    total = len(closes)
    log(f"Price range: ${min(closes):.0f} - ${max(closes):.0f}")
    
    def calc_rsi(p, period):
        if len(p) < period+1: return None
        g,l=[],[]
        for i in range(1,len(p)):
            c=p[i]-p[i-1]
            if c>0: g.append(c); l.append(0.0)
            else: g.append(0.0); l.append(abs(c))
        ag=sum(g[:period])/period; al=sum(l[:period])/period
        for i in range(period,len(g)):
            ag=(ag*(period-1)+g[i])/period; al=(al*(period-1)+l[i])/period
        return 100.0-100.0/(1.0+ag/al) if al>1e-10 else 100.0
    
    def calc_ema(p, period):
        if len(p)<period: return None
        m=2/(period+1); r=sum(p[:period])/period
        for x in p[period:]: r=(x-r)*m+r
        return r
    
    def calc_boll(p, period=20, std=2):
        if len(p)<period: return None,None,None
        s=sum(p[-period:])/period
        v=sum((x-s)**2 for x in p[-period:])/period; sd=v**0.5
        return s, s+std*sd, s-std*sd
    
    WARMUP = 169
    LOOKBACK = 300
    LOOKAHEADS = [3, 6, 12]  # 45min, 1.5h, 3h
    
    # Signal tracking: {signal_type: {lookahead: [change_pcts]}}
    sig_data = {}
    for s in ["boll_up","boll_low","rsi_low","rsi_high","vdrop","vshrink","vtop"]:
        sig_data[s] = {la: [] for la in LOOKAHEADS}
    sig_data["dip_zone"] = {la: [] for la in LOOKAHEADS}
    sig_data["top_zone"] = {la: [] for la in LOOKAHEADS}
    
    for i in range(WARMUP, total - max(LOOKAHEADS)):
        start = max(0, i - LOOKBACK)
        c = closes[start:i+1]
        v = volumes[start:i+1]
        
        r = {}
        for p in [6,14,21]:
            x = calc_rsi(c,p)
            if x is not None: r[p]=x
        if len(r)<3: continue
        
        pr = c[-1]
        bm,bu,bl = calc_boll(c)
        ema_s = calc_ema(c,144)
        ema_l = calc_ema(c,169) if len(c)>=169 else None
        avg_v = (sum(v[-21:-1])/20) if len(v)>=22 else 1
        vr = v[-1]/avg_v if avg_v>0 else 1
        pch = (c[-1]-c[-2])/c[-2]*100 if len(c)>=2 else 0
        
        active = []
        if bu and pr>bu: active.append("boll_up")
        if bl and pr<bl: active.append("boll_low")
        for p in [6,14,21]:
            if p in r and r[p]<30: active.append("rsi_low")
            if p in r and r[p]>70: active.append("rsi_high")
        if len(v)>=22:
            if vr>1.5 and pch<-0.5: active.append("vdrop")
            if vr<0.5 and pch>0.3 and r[6]>65: active.append("vshrink")
            if vr>1.5 and abs(pch)<0.2: active.append("vtop")
        
        for la in LOOKAHEADS:
            if i+la >= total: continue
            chg = (closes[i+la] - pr) / pr * 100
            
            for sig in active:
                sig_data[sig][la].append(chg)
            if "rsi_low" in active and "boll_low" in active:
                sig_data["dip_zone"][la].append(chg)
            if "rsi_high" in active and "boll_up" in active:
                sig_data["top_zone"][la].append(chg)
    
    log("")
    log("="*70)
    log("SIGNAL WIN-RATE REPORT (15m, 1000 candles)")
    log("="*70)
    
    for sig_name, label, win_dir in [
        ("boll_up","BOLL上轨(涨太快)","down"),
        ("boll_low","BOLL下轨(跌太狠)","up"),
        ("rsi_low","RSI低位(跌过头)","up"),
        ("rsi_high","RSI高位(涨过头)","down"),
        ("vdrop","放量下跌(砸盘)","up"),
        ("vshrink","缩量上涨背离","down"),
        ("vtop","放量不涨(多空打架)","flat"),
        ("dip_zone","RSI低位+BOLL下轨(做多区)","up"),
        ("top_zone","RSI高位+BOLL上轨(做空区)","down"),
    ]:
        log(f"\n--- {label} ---")
        for la in LOOKAHEADS:
            data = sig_data[sig_name][la]
            if not data: continue
            n = len(data)
            up = sum(1 for x in data if x > 0)
            dn = sum(1 for x in data if x < 0)
            fl = n - up - dn
            avg = sum(data)/n
            
            if win_dir == "up":
                wins = up
            elif win_dir == "down":
                wins = dn
            else:
                wins = fl
            
            la_s = f"{la*15}min"
            log(f"  {la_s}: {n}次 | 涨{up}({up*100//n}%) 跌{dn}({dn*100//n}%) 平{fl}({fl*100//n}%) | 均{avg:+.2f}% | 方向胜率{wins*100//n}%")
        
        # Best/worst examples
        for la in LOOKAHEADS:
            data = sig_data[sig_name][la]
            if not data: continue
            n = len(data)
            if n > 3:
                sorted_d = sorted(data)
                log(f"  最佳3(+{la*15}min): {', '.join(f'{x:+.2f}%' for x in sorted_d[-3:])}")
                log(f"  最差3(+{la*15}min): {', '.join(f'{x:+.2f}%' for x in sorted_d[:3])}")
    
    log("")
    log("=== DONE ===")
    
except Exception as e:
    log("FATAL: " + str(e))
