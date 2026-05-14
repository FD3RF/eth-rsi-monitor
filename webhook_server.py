#!/usr/bin/env python3
"""TradingView Webhook Receiver for Wangcai"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json, requests

TG_TOKEN = "8640664409:AAHPZIZd1YGa6jCwXziM01qoJ0RJwCfG-LM"
TG_CHAT_ID = "8410098965"

def send_tg(text):
    for _ in range(3):
        try:
            r = requests.post("https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
                json={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)
            if r.status_code == 200: return
        except:
            pass

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode()
        try:
            data = json.loads(raw) if raw.startswith("{") else {"text": raw}
            msg = data.get("text", data.get("message", raw))
            send_tg("Alert: " + msg[:300])
        except Exception as e:
            send_tg("Webhook err: " + str(e))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"wangcai webhook running")
    def log_message(self, fmt, *args):
        print("[WEBHOOK] %s %s %s" % args)

if __name__ == "__main__":
    port = 8765
    s = HTTPServer(("0.0.0.0", port), H)
    print("Webhook on port %d" % port)
    s.serve_forever()
