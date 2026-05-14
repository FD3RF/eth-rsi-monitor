#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETH RSI V8 - 回调顺趋势系统
┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
砍掉所有FSM、状态机、regime、MSL。
只剩3条规则：
  1. 1H EMA144/169 = 趋势方向
  2. 15M RSI(<40或>60)或MACD金死叉 = 回调信号
  3. 只做第一次触发
输出：TRADE / NO TRADE
"""
import requests, time, threading, json, os, sys, traceback
from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler

# ========== 日志 ==========
LOG_FILE = "v8_output.log"
_log = logging.getLogger('v8')
_log.setLevel(logging.DEBUG)
_log.handlers.clear()
try:
    _fh = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
    _fh.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', datefmt='%H:%M:%S'))
    _log.addHandler(_fh)
except:
    logging.basicConfig(format='[%(asctime)s] %(message)s', stream=sys.stderr, level=logging.DEBUG)
    _log = logging.getLogger('v8_fallback')

def log(m): _log.debug(str(m))

# ========== 配置 ==========
GATEIO = "https://api.gateio.ws/api/v4"
CONTRACT = "ETH_USDT"
BARK_KEY = "gbRTde9uu3C8AwZBqorEj8"
CHECK_INTERVAL = 60

# EMA趋势参数
EMA_SHORT = 144
EMA_LONG = 169

# 回调参数 (15M)
RSI_PERIODS = [6, 14]
RSI_PULLBACK_LONG = 40    # 多头回调: RSI < 40
RSI_PULLBACK_SHORT = 60   # 空头回调: RSI > 60

# MACD
MACD_FAST = 6; MACD_SLOW = 13; MACD_SIGNAL = 5

# 冷却
TRIGGER_COOLDOWN = 7200  # 2小时，防止重复触发

# 信号成绩跟踪
SIGNAL_CSV = "v8_signals.csv"
EVAL_WINDOWS = {"15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240}
# 计价单位: 分钟


# ========== 信号成绩记录器（可验证闭环） ==========
class SignalLogger:
    """
    每个 TRADE 信号都有历史成绩。
    记录 → 等待 → 结算 → 统计
    """
    
    def __init__(self):
        self._open_signals = {}  # id -> SignalRecord
        self._next_id = 1
        self._init_csv()
    
    def _init_csv(self):
        if os.path.exists(SIGNAL_CSV):
            # 读取已有信号恢复next_id
            try:
                with open(SIGNAL_CSV, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip() and not line.startswith("id"):
                            parts = line.split(",")
                            if parts[0].isdigit():
                                self._next_id = max(self._next_id, int(parts[0]) + 1)
            except:
                self._next_id = 1
            return
        with open(SIGNAL_CSV, 'w', newline='', encoding='utf-8') as f:
            cols = ["id","time","direction","price","r6","r14","ema_s","ema_l",
                    "reason","stop_time","result_15m","result_30m","result_1h",
                    "result_2h","result_4h","max_up_2h","max_down_2h"]
            f.write(",".join(cols) + "\n")
    
    def record(self, direction, price, r6, r14, ema_s, ema_l, reason, now):
        """记录一次TRADE信号"""
        sig_id = self._next_id
        self._next_id += 1
        entry = {
            "id": sig_id,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "direction": direction,
            "entry_price": price,
            "r6": r6, "r14": r14,
            "ema_s": ema_s, "ema_l": ema_l,
            "reason": reason,
            "start_ts": now,
            "highest": price,
            "lowest": price,
            "results": {},  # window_minutes -> True/False/None
            "max_up_2h": 0,
            "max_down_2h": 0,
        }
        self._open_signals[sig_id] = entry
        
        # 立即写入CSV（结果列为空）
        with open(SIGNAL_CSV, 'a', newline='', encoding='utf-8') as f:
            row = [str(sig_id), entry["time"], direction, "%.2f" % price,
                   "%.1f" % (r6 or 0), "%.1f" % (r14 or 0),
                   "%.2f" % (ema_s or 0), "%.2f" % (ema_l or 0),
                   reason, "", "", "", "", "", "", ""]
            f.write(",".join(row) + "\n")
        
        return sig_id
    
    def update(self, m15_closes, now):
        """
        每轮扫描时更新所有未结算信号：
        - 记录最高/最低价
        - 检查是否到达结算窗口
        """
        if not self._open_signals:
            return []
        
        current_price = m15_closes[-1] if m15_closes else None
        if not current_price:
            return []
        
        completed = []
        for sig_id, sig in list(self._open_signals.items()):
            elapsed = (now - sig["start_ts"]) / 60  # 分钟
            
            # 更新高低价
            sig["highest"] = max(sig["highest"], current_price)
            sig["lowest"] = min(sig["lowest"], current_price)
            
            # 检查各结算窗口
            for win_name, win_min in EVAL_WINDOWS.items():
                if win_name in sig["results"]:
                    continue  # 已结算
                if elapsed >= win_min:
                    # 结算: 看方向是否有利
                    is_long = sig["direction"] == "LONG"
                    if is_long:
                        # 做多: 最高价上涨超过最低价下跌 = 胜利
                        up_pct = (sig["highest"] - sig["entry_price"]) / sig["entry_price"] * 100
                        down_pct = (sig["entry_price"] - sig["lowest"]) / sig["entry_price"] * 100
                        won = up_pct > down_pct and up_pct > 0.3
                    else:
                        # 做空: 最低价下跌超过最高价上涨 = 胜利
                        down_pct = (sig["entry_price"] - sig["lowest"]) / sig["entry_price"] * 100
                        up_pct = (sig["highest"] - sig["entry_price"]) / sig["entry_price"] * 100
                        won = down_pct > up_pct and down_pct > 0.3
                    
                    sig["results"][win_name] = won
                    
                    # 如果是2h窗口，记录max_up/down
                    if win_name == "2h":
                        sig["max_up_2h"] = up_pct
                        sig["max_down_2h"] = down_pct
            
            # 检查信号是否全部结算完毕
            all_settled = all(wn in sig["results"] for wn in EVAL_WINDOWS)
            if all_settled:
                completed.append(sig)
                # 写回CSV更新结果
                self._update_csv(sig)
                del self._open_signals[sig_id]
        
        return completed
    
    def _update_csv(self, sig):
        """更新CSV中对应信号行的结果列"""
        rows = []
        with open(SIGNAL_CSV, 'r', encoding='utf-8') as f:
            header = f.readline().strip()
            rows.append(header)
            for line in f:
                parts = line.strip().split(",")
                if parts[0] == str(sig["id"]):
                    # 更新结果
                    parts[10] = "W" if sig["results"].get("15m") else ("L" if "15m" in sig["results"] else "")
                    parts[11] = "W" if sig["results"].get("30m") else ("L" if "30m" in sig["results"] else "")
                    parts[12] = "W" if sig["results"].get("1h") else ("L" if "1h" in sig["results"] else "")
                    parts[13] = "W" if sig["results"].get("2h") else ("L" if "2h" in sig["results"] else "")
                    parts[14] = "W" if sig["results"].get("4h") else ("L" if "4h" in sig["results"] else "")
                    parts[15] = "%.2f" % sig.get("max_up_2h", 0)
                    parts[16] = "%.2f" % sig.get("max_down_2h", 0)
                    parts[9] = datetime.now().strftime("%Y-%m-%d %H:%M")  # stop_time
                rows.append(",".join(parts))
        with open(SIGNAL_CSV, 'w', newline='', encoding='utf-8') as f:
            f.write("\n".join(rows) + "\n")
    
    def report(self):
        """返回统计数据 dict"""
        stats = {"total": 0, "wins_2h": 0, "losses_2h": 0, "long": 0, "short": 0}
        if not os.path.exists(SIGNAL_CSV):
            return stats
        with open(SIGNAL_CSV, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith("id"): continue
                parts = line.strip().split(",")
                if len(parts) < 14: continue
                stats["total"] += 1
                if parts[2] == "LONG": stats["long"] += 1
                else: stats["short"] += 1
                r = parts[13].strip()
                if r == "W": stats["wins_2h"] += 1
                elif r == "L": stats["losses_2h"] += 1
        return stats


# ========== 指标计算 ==========
def calc_rsi(p, period):
    if not p or len(p) < period+1: return None
    g, l = [], []
    for i in range(1, len(p)):
        c = p[i]-p[i-1]
        if c > 0: g.append(c); l.append(0.0)
        else: g.append(0.0); l.append(abs(c))
    ag, al = sum(g[:period])/period, sum(l[:period])/period
    for i in range(period, len(g)):
        ag = (ag*(period-1)+g[i])/period
        al = (al*(period-1)+l[i])/period
    return 100-100/(1+ag/al) if al > 1e-10 else 100

def calc_ema(p, period):
    if len(p) < period: return None
    k = 2/(period+1); r = sum(p[:period])/period
    for x in p[period:]: r = (x-r)*k+r
    return r

def calc_macd(closes, fast=6, slow=13, signal=5):
    if len(closes) < slow+signal+2: return None, None, None, False, False, ""
    def ema_s(data, period):
        k = 2/(period+1); r = sum(data[:period])/period
        res = [r]
        for x in data[period:]: r = (x-r)*k+r; res.append(r)
        return res
    e12 = ema_s(closes, fast); e26 = ema_s(closes, slow)
    if not e12 or not e26: return None, None, None, False, False, ""
    off = len(e12)-len(e26); e12 = e12[off:]
    macd = [e12[i]-e26[i] for i in range(len(e12))]
    sig = ema_s(macd, signal)
    if not sig or len(sig) < 2: return None, None, None, False, False, ""
    cm, cs = macd[-1], sig[-1]; pm, ps = macd[-2], sig[-2]
    return cm, cs, cm-cs, (pm <= ps and cm > cs), (pm >= ps and cm < cs), "零轴上" if cm > 0 else "零轴下"


# ========== 数据获取 ==========
def get_data(iv, limit=200):
    """从Gate.io获取K线数据 (返回dict格式 {o,h,l,c,v,t})"""
    interval_map = {"15m":"15m","1h":"1h"}
    for a in range(3):
        try:
            r = requests.get("%s/futures/usdt/candlesticks" % GATEIO,
                params={"contract":CONTRACT,"interval":interval_map.get(iv,iv),"limit":limit},
                timeout=8)
            if r.status_code != 200: continue
            data = r.json()  # list of dicts: [{o, h, l, c, v, t, sum}, ...]
            closes = [float(x['c']) for x in data]
            volumes = [float(x['v']) for x in data]
            closes.reverse(); volumes.reverse()
            return closes, volumes
        except Exception as e:
            if a < 2: time.sleep(1)
    return None, None


# ========== 推送 ==========
def push(title, body):
    for a in range(3):
        try:
            r = requests.post("https://api.day.app/push", json={
                "device_key": BARK_KEY,
                "title": title,
                "body": body,
                "group": "ETH-RSI-V8",
                "level": "timeSensitive",
                "badge": 1,
            }, timeout=8)
            if r.status_code == 200 and r.json().get("code") == 200:
                log("push ok: %s" % title[:30])
                return True
        except:
            if a < 2: time.sleep(1)
    log("push fail: %s" % title[:30])
    return False


# ========== 回调趋势检测 ==========
class TrendPullbackDetector:
    """
    只做一件事：判断"现在是不是回调顺趋势"
    """
    
    def __init__(self):
        self._triggered = False       # 当前信号是否已触发
        self._trigger_time = 0        # 最后触发时间戳
        self._trigger_direction = ""  # "LONG" 或 "SHORT"
    
    def check(self, h1_closes, m15_closes, m15_volumes):
        """
        返回: (decision, direction, reason, detail_dict)
        decision: "TRADE" / "NO_TRADE"
        direction: "LONG" / "SHORT" / ""
        """
        now = time.time()
        result = {"decision": "NO_TRADE", "direction": "", "reason": "",
                  "h1_ema_s": None, "h1_ema_l": None, "h1_trend": "",
                  "m15_r6": None, "m15_r14": None, "m15_macd_golden": False,
                  "m15_macd_death": False}
        
        # ── 1. 1H趋势判断 ──
        if not h1_closes or len(h1_closes) < EMA_LONG + 5:
            result["reason"] = "数据不足"
            return result
        
        h1_ema_s = calc_ema(h1_closes, EMA_SHORT)
        h1_ema_l = calc_ema(h1_closes, EMA_LONG)
        if h1_ema_s is None or h1_ema_l is None:
            result["reason"] = "EMA数据不足"
            return result
        
        result["h1_ema_s"] = round(h1_ema_s, 2)
        result["h1_ema_l"] = round(h1_ema_l, 2)
        trend_up = h1_ema_s > h1_ema_l
        result["h1_trend"] = "↑上涨" if trend_up else "↓下跌"
        
        # ── 2. 趋势不存在 → 不做 ──
        ema_gap = abs(h1_ema_s - h1_ema_l) / h1_ema_l * 100
        if ema_gap < 0.05:  # EMA太接近 = 无趋势
            result["reason"] = "无趋势(EMA纠缠)"
            return result
            result["reason"] = "无趋势(EMA纠缠)"
            return result
        
        # ── 3. 15M回调判断 ──
        if not m15_closes or len(m15_closes) < 30:
            result["reason"] = "15M数据不足"
            return result
        
        r6 = calc_rsi(m15_closes, 6)
        r14 = calc_rsi(m15_closes, 14)
        mv, ms, mh, golden, death, mz = calc_macd(m15_closes)
        
        result["m15_r6"] = round(r6, 1) if r6 else None
        result["m15_r14"] = round(r14, 1) if r14 else None
        result["m15_macd_golden"] = golden
        result["m15_macd_death"] = death
        
        # ── 4. 决定交易方向 ──
        trade_signal = False
        direction = ""
        reason = ""
        
        if trend_up:
            # 多头趋势：找15M回调做多
            if r6 is not None and r6 < RSI_PULLBACK_LONG:
                trade_signal = True
                direction = "LONG"
                reason = "RSI6=%.0f回调至多头趋势" % r6
            elif golden:
                trade_signal = True
                direction = "LONG"
                reason = "MACD金叉回调至多头趋势"
        else:
            # 空头趋势：找15M反弹做空
            if r6 is not None and r6 > RSI_PULLBACK_SHORT:
                trade_signal = True
                direction = "SHORT"
                reason = "RSI6=%.0f反弹至空头趋势" % r6
            elif death:
                trade_signal = True
                direction = "SHORT"
                reason = "MACD死叉反弹至空头趋势"
        
        if not trade_signal:
            result["reason"] = "无回调信号"
            self._triggered = False
            return result
        
        # ── 5. 只做第一次触发 ──
        # 检查是否已经触发过同方向的信号且还在冷却期
        if direction == self._trigger_direction and (now - self._trigger_time < TRIGGER_COOLDOWN):
            remain = int(TRIGGER_COOLDOWN - (now - self._trigger_time))
            result["reason"] = "冷却中(%dm)" % (remain // 60)
            return result
        
        # 方向变了 → 重置冷却
        if direction != self._trigger_direction:
            self._triggered = False
        
        # ── 6. 首次触发 ──
        if not self._triggered or direction != self._trigger_direction:
            self._triggered = True
            self._trigger_time = now
            self._trigger_direction = direction
            result["decision"] = "TRADE"
            result["direction"] = direction
            result["reason"] = reason
            return result
        
        result["reason"] = "已触发过"
        return result


# ========== 健康监控 ==========
class HealthMonitor:
    def __init__(self):
        self._last_ok = time.time()
        self._heartbeat_interval = 14400  # 4小时
    
    def check(self):
        now = time.time()
        if now - self._last_ok > self._heartbeat_interval:
            self._last_ok = now
            return True
        return False


# ========== 主循环 ==========
def main():
    log("=" * 50)
    log("  ETH RSI V8 - 回调顺趋势系统")
    log("  时间: %s" % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    log("  规则: 1H EMA趋势 + 15M RSI回调")
    log("=" * 50)
    log("  Bark分组: ETH-RSI-V8 (V6.3=V63, V7已停用)")
    log("=" * 50)
    
    detector = TrendPullbackDetector()
    logger = SignalLogger()
    hm = HealthMonitor()
    _last_report_time = 0
    
    while True:
        try:
            # ── 获取数据 ──
            h1_closes, _ = get_data("1h", 200)
            m15_closes, m15_volumes = get_data("15m", 100)
            
            if not h1_closes or not m15_closes:
                log("数据获取失败: h1=%s m15=%s" % ("OK" if h1_closes else "FAIL", "OK" if m15_closes else "FAIL"))
                time.sleep(CHECK_INTERVAL)
                continue
            log("数据: 1h %d根, 15m %d根" % (len(h1_closes), len(m15_closes)))
            
            now = time.time()
            
            # ── 检测 ──
            r = detector.check(h1_closes, m15_closes, m15_volumes)
            
            # ── 更新信号成绩（检查是否有信号到期结算） ──
            completed = logger.update(m15_closes, now)
            for sig in completed:
                result_str = "W" if sig["results"].get("2h") else "L"
                log("SIGNAL %d CLOSED: %s 2h=%s up=%.1f%% down=%.1f%%" % (
                    sig["id"], sig["direction"], result_str,
                    sig.get("max_up_2h", 0), sig.get("max_down_2h", 0)))
                # 结算后通知
                push("📊 V8结算",
                    "方向：%s\n"
                    "入场：$%.0f\n"
                    "2h结果：%s\n"
                    "⏰ %s" % (
                        "做多" if sig["direction"] == "LONG" else "做空",
                        sig["entry_price"],
                        "✅ 盈利" if sig["results"].get("2h") else "❌ 亏损",
                        datetime.now().strftime("%H:%M")))
            
            # ── 推送 ──
            if r["decision"] == "TRADE":
                r6v = r["m15_r6"] or 0
                r14v = r["m15_r14"] or 0
                price = m15_closes[-1]
                
                sig_id = logger.record(r["direction"], price, r6v, r14v,
                    r["h1_ema_s"], r["h1_ema_l"], r["reason"], now)
                
                direction_cn = "做多" if r["direction"] == "LONG" else "做空"
                # 强度判断（简单三档）
                intensity = "高" if (r6v < 35 or r6v > 65) else "中" if (r6v < 42 or r6v > 58) else "低"
                reason_short = r["reason"][:20] if r["reason"] else "信号触发"
                
                title = "📊 V8"
                body = (
                    "方向：%s\n"
                    "强度：%s\n"
                    "理由：%s\n"
                    "⏰ %s"
                ) % (direction_cn, intensity, reason_short, datetime.now().strftime("%H:%M"))
                push(title, body)
                log("TRADE #%d: %s | %s | $%.0f" % (sig_id, r["direction"], r["reason"], price))
            else:
                # 不做—每5分钟记录一次
                r6v = r["m15_r6"] if r["m15_r6"] is not None else 0
                r14v = r["m15_r14"] if r["m15_r14"] is not None else 0
                log("SCAN: %s | EMA %.0f/%.0f %s | RSI6=%.1f RSI14=%.1f | %s" % (
                    r["h1_trend"], r["h1_ema_s"] or 0, r["h1_ema_l"] or 0,
                    r["h1_trend"], r6v, r14v,
                    r.get("reason", ""),
                ))
            
            # ── 每日成绩单推送 ──
            if now - _last_report_time > 86400:  # 24h
                _last_report_time = now
                report = logger.report()
                s = logger.report()
                wr = s["wins_2h"]/max(s["wins_2h"]+s["losses_2h"],1)*100
                push("📊 V8日报",
                    "信号：%d单\n"
                    "2h胜率：%.0f%%\n"
                    "多/空：%d/%d" % (
                        s["total"], wr, s["long"], s["short"]))
                log("日成绩单已推送")
            
            # ── 4h心跳 ──
            if hm.check():
                push("💚 V8", "系统正常")
            
            time.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            log("main error: %s" % e)
            log("TRACEBACK: %s" % traceback.format_exc())
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
