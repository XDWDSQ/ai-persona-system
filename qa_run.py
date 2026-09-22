# -*- coding: utf-8 -*-
"""QA 全链路包装：供 qa_screenshot.js / qa_flow.js 使用的隔离服务实例。

与真实运行的差别（全部进程内生效，不碰磁盘真数据）：
  1. 关闭登录门禁（密钥仍走掩码逻辑）
  2. 运行时数据隔离：sessions / uploads / 记忆与状态 全部重定向到临时目录
  3. 拦截会花钱或写真实配置的端点（问候/TTS/模型列表/天气刷新/角色动态/配置保存/切角色）
  4. /api/chat 替换为桩流式实现（SSE 协议与真实一致）：
       消息含 [fail]  → 先吐部分内容再报错（验证失败重试链路）
       消息含 [empty] → 空回复（验证空回复失败气泡）
       其他          → 分四段增量输出 + done 事件（验证流式渲染/剥样式标记）
"""
import asyncio
import json
import tempfile
from pathlib import Path

import uvicorn
import server
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

# ---- 1) 免密门禁 ----
server._access_token = lambda: None

# ---- 1.5) 禁止任何真实外部调用 ----
# 下面的路径重定向发生在 import 之后，模块级路径常量（config/weather/role_news
# 缓存等）仍指向真实数据，而 lifespan 启动预取会读真实 config：若激活角色开了
# 角色动态自动刷新，QA 实例一启动就会发真实搜索 + 云端总结请求（烧 token）。
# QA 实例必须完全离线：统一关闭天气与角色动态的自动刷新/巡检/读时触发。
server._role_news_auto_refresh = lambda *a, **k: False
server._weather_enabled = lambda *a, **k: False

# ---- 2) 运行时数据隔离 ----
_qa_tmp = Path(tempfile.mkdtemp(prefix="qa_persona_"))
server.SESSIONS_PATH = _qa_tmp / "sessions.json"
server.SESSIONS_PATH.write_text(json.dumps({"sessions": [], "deleted": []}), encoding="utf-8")
server._sess_cache = {"_mtime_ns": 0, "_value": {"sessions": [], "deleted": []}}
server.DATA_DIR = _qa_tmp
server.UPLOAD_DIR = _qa_tmp / "uploads"
server.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
for _r in server.app.router.routes:
    if getattr(_r, "path", "") == "/uploads":
        _r.app = StaticFiles(directory=str(server.UPLOAD_DIR))

# ---- 3) 拦截端点 ----
_BLOCKED = {
    "/api/greeting", "/api/role-news/refresh", "/api/tts", "/api/llm-models",
    "/api/weather/refresh", "/api/role-news/update", "/api/config", "/api/roles/apply",
}
_replaced = {"/api/chat"}
_keep = []
for _r in server.app.router.routes:
    _p = getattr(_r, "path", "")
    _m = getattr(_r, "methods", set())
    if _p in _BLOCKED and "POST" in _m:
        continue
    if _p in _replaced and "POST" in _m:
        continue
    _keep.append(_r)
server.app.router.routes[:] = _keep

for _p in _BLOCKED:
    def _mk(_p=_p):
        async def _h():
            raise HTTPException(403, "QA 实例已拦截该端点")
        return _h
    server.app.post(_p)(_mk())


# ---- 4) 桩流式 /api/chat ----
_SSE_NL = chr(10) * 2  # SSE 事件以空行结尾


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + _SSE_NL


_CLEAN = "好，我在。今天训练刚结束，状态还行。"
_empty_seen = set()


async def _qa_chat(req: dict):
    msg = req.get("message") or ""

    async def _gen():
        if "[fail]" in msg:
            yield _sse({"d": "这部分先流出来，"})
            await asyncio.sleep(0.2)
            yield _sse({"err": "QA 模拟生成失败"})
            return
        if "[empty]" in msg:
            # 首次返回空（验证失败气泡），重试（同名消息第二次起）返回正常内容（验证重试链路）
            if msg in _empty_seen:
                for _piece in ("好，", "我在。", "重试成功。"):
                    await asyncio.sleep(0.12)
                    yield _sse({"d": _piece})
                yield _sse({"done": True, "clean": "好，我在。重试成功。", "style": "平静",
                            "searched": False, "vision_used": False})
                return
            _empty_seen.add(msg)
            yield _sse({"done": True, "clean": "", "style": "", "searched": False, "vision_used": False})
            return
        for _piece in ("好，", "我在。", "今天训练刚结束，", "状态还行。"):
            await asyncio.sleep(0.15)
            yield _sse({"d": _piece})
        yield _sse({"done": True, "clean": _CLEAN, "style": "平静",
                    "searched": False, "vision_used": False})

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


server.app.post("/api/chat")(_qa_chat)

# 桩路由是新 append 的，排在末尾静态挂载（Mount "/" 与 /uploads）之后会被遮蔽；
# 稳定排序把 Mount 挪到列表末尾，其余相对顺序不变
from starlette.routing import Mount as _Mount
server.app.router.routes.sort(key=lambda r: isinstance(r, _Mount))

if __name__ == "__main__":
    uvicorn.run(server.app, host="127.0.0.1", port=8010, log_level="warning")
