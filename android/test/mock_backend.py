# -*- coding: utf-8 -*-
"""APK 测试用模拟后端：复刻真实服务的核心端点与鉴权行为。

- POST /api/login        校验 token（默认 520TDJ），种 httponly cookie ai_token（30 天）
- 其余 /api/*、/uploads/* 需 cookie，否则 302 → /login（与 server.py 一致）
- /api/chat              返回固定回复（模拟 LLM）
- /api/sync/stream       SSE 推送（含 15s ping 保活）
- /api/tts/file          返回一段 WAV 音频（合成方波，可实际播放）
- /api/upload            接收 multipart 上传并回显文件名

用法：python mock_backend.py [端口]   （默认 8123）
"""
import base64
import json
import re
import struct
import sys
import time
import wave
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

TOKEN = "520TDJ"
COOKIE = "ai_token"
AUTH_FREE = ("/api/health", "/api/login", "/login")


def make_wav(duration=30.0, freq=440.0, sample_rate=8000):
    """生成一段可播放的 WAV（方波）"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        frames = bytearray()
        for i in range(int(duration * sample_rate)):
            v = int(12000 if (i * freq / sample_rate) % 1 < 0.5 else -12000)
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[mock] %s %s\n" % (self.command, self.path))

    # ---- 基础工具 ----

    def _auth_ok(self):
        cookie = self.headers.get("Cookie", "")
        return TOKEN in cookie  # 宽松匹配：cookie 中包含 ai_token=<TOKEN>

    def _send(self, code, body, headers=None, is_json=True):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        if is_json:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    # ---- 路由 ----

    def do_GET(self):
        path = urlparse(self.path).path
        if path in AUTH_FREE and path != "/login":
            if path == "/api/health":
                self._send(200, {"ok": True, "deps": {"llm": "cloud", "tts": "mock"}})
                return
        if path == "/login":
            self._send(200, "<h1>登录页(mock)</h1>", is_json=False)
            return
        if not self._auth_ok():
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/status":
            self._send(200, {"provider": "cloud", "roles": {
                "xiaoni": {"name": "小拟", "desc": "mock"},
                "dashuai": {"name": "大帅", "desc": "mock"},
            }})
        elif path == "/api/roles":
            self._send(200, ["xiaoni", "dashuai"])
        elif path == "/api/state":
            self._send(200, {"role": "dashuai", "emotion": "平静", "energy": 80})
        elif path == "/api/sessions":
            self._send(200, {"sessions": []})
        elif path == "/api/role-news":
            self._send(200, {"dashuai": {"text": "mock 动态", "updated_at": time.time()}})
        elif path == "/api/sync/stream":
            self._stream_sse()
        elif path.startswith("/api/tts/file"):
            wav = make_wav()
            self._send(200, wav, {"Content-Type": "audio/wav"}, is_json=False)
        elif path.startswith("/uploads/"):
            # 1x1 红色 PNG
            png = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
            self._send(200, png, {"Content-Type": "image/png"}, is_json=False)
        else:
            self._send(404, {"detail": "mock 404: %s" % path})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/login":
            try:
                payload = json.loads(self._read_body() or b"{}")
            except Exception:
                self._send(400, {"detail": "bad json"})
                return
            if payload.get("token") != TOKEN:
                time.sleep(1)  # 与真实服务一致的防爆破退避
                self._send(401, {"detail": "访问口令错误"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header(
                "Set-Cookie",
                "ai_token=%s; Path=/; HttpOnly; Max-Age=2592000" % TOKEN)
            self.send_header("Content-Length", "11")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            return
        if not self._auth_ok():
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/chat":
            try:
                payload = json.loads(self._read_body() or b"{}")
            except Exception:
                payload = {}
            msg = (payload.get("messages") or [{}])[-1].get("content", "")
            self._send(200, {"reply": "（模拟回复）收到：%s" % msg[:80], "style": "normal"})
        elif path == "/api/tts":
            self._read_body()
            wav = make_wav()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("X-TTS-Cache", "mock-hash-001")
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)
        elif path == "/api/greeting":
            self._send(200, {"reply": "（模拟问候）老公，我在呀", "style": "normal"})
        elif path == "/api/upload":
            body = self._read_body()
            m = re.search(rb'filename="([^"]*)"', body)
            name = m.group(1).decode("utf-8", "ignore") if m else "unknown"
            self._send(200, {"ok": True, "files": [{"filename": name, "size": len(body)}]})
        elif path == "/api/llm-models":
            self._send(200, {"models": ["mock-1", "mock-2"]})
        else:
            self._send(404, {"detail": "mock 404: %s" % path})

    def do_PUT(self):
        if not self._auth_ok():
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(200, {"ok": True})

    def do_DELETE(self):
        if not self._auth_ok():
            self.send_response(302)
            self.send_header("Location", "/login")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(200, {"ok": True})

    # ---- SSE ----

    def _stream_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(b"retry: 3000\n\n")
        self.wfile.flush()
        # 推送一条会话更新事件，然后保持连接（模拟 15s ping）
        try:
            self.wfile.write(
                ('data: {"type":"sessions_updated","ts":%d}\n\n' % int(time.time() * 1000)).encode())
            self.wfile.flush()
            deadline = time.time() + 8
            while time.time() < deadline:
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8123
    print("mock backend on 127.0.0.1:%d, token=%s" % (port, TOKEN))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
