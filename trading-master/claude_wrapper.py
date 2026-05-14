#!/usr/bin/env python3
"""API 调用器（多Provider自动切换）
支持: 智谱GLM / 硅基流动 / DeepSeek"""
import requests, json, os, time

ENDPOINTS = [
    # Key-0: 硅基流动 (DeepSeek-V4-Flash)
    {"name": "硅基流动", "key": "sk-eevlkxrnmfgtoxyijmftmcexvqrdkjkokmhszpiebjfwhgvm",
     "url": "https://api.siliconflow.cn/v1/chat/completions",
     "model": "deepseek-ai/DeepSeek-V4-Flash"},
    # Key-1: DeepSeek DashScope (备用)
    {"name": "DeepSeek", "key": "sk-6c0916d82cb84a57b5b76fe5b0107cfd",
     "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
     "model": "deepseek-v4-pro"},
]

SWITCH_FILE = "/opt/trading-master/active_key.txt"

def get_ep():
    try:
        with open(SWITCH_FILE) as f:
            idx = int(f.read().strip())
            if idx < len(ENDPOINTS): return ENDPOINTS[idx], idx
    except: pass
    return ENDPOINTS[0], 0

def save_ep(idx):
    with open(SWITCH_FILE, "w") as f: f.write(str(idx))

def ask(messages, max_tokens=1000, timeout=60) -> str:
    last_err = ""
    for attempt in range(len(ENDPOINTS) + 1):
        ep, idx = get_ep()
        try:
            r = requests.post(ep["url"], json={
                "model": ep["model"],
                "messages": messages,
                "max_tokens": max_tokens,
                "stream": False,
            }, headers={"Authorization": f"Bearer {ep['key']}", "Content-Type": "application/json"}, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                return data["choices"][0]["message"]["content"]
            else:
                last_err = f"HTTP {r.status_code}"
                print(f"[切换] {ep['name']} {r.status_code}, 切到 {(idx+1)%len(ENDPOINTS)}", flush=True)
                save_ep((idx + 1) % len(ENDPOINTS))
        except Exception as e:
            last_err = str(e)[:50]
            print(f"[切换] {ep['name']} ERR, 切到 {(idx+1)%len(ENDPOINTS)}", flush=True)
            save_ep((idx + 1) % len(ENDPOINTS))
    return f"大师暂时无法响应 ({last_err})"

if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "ping"
    print(ask([{"role": "user", "content": q}]))
