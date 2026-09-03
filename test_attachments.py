# -*- coding: utf-8 -*-
"""附件上传 + 附件聊天/视觉通道测试。

运行：python test_attachments.py
"""
import asyncio
import copy
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

_FAIL = 0
_UPLOAD_CLEANUP: list[Path] = []


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


def base_cfg():
    cfg = copy.deepcopy(server.load_config(with_env=False))
    cfg["role_engine"] = {"enabled": False}
    cfg["provider"] = "cloud"
    cfg["cloud"] = {"base_url": "http://fake/v1", "model": "fake", "api_key": "none"}
    cfg.setdefault("vision", {})["enabled"] = True
    return cfg


def test_upload(client):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    r = client.post(
        "/api/upload",
        files=[("files", ("test.png", png, "image/png"))],
    )
    data = r.json()
    check(
        "上传返回图片附件元数据",
        r.status_code == 200 and data["files"][0]["kind"] == "image",
        f"status={r.status_code} data={data}",
    )
    url = data["files"][0]["url"]
    path = server.UPLOAD_DIR / Path(url).name
    check("上传文件已落盘", path.is_file(), str(path))
    _UPLOAD_CLEANUP.append(path)

    r2 = client.post(
        "/api/upload",
        files=[("files", ("evil.exe", b"MZ", "application/octet-stream"))],
    )
    check("不安全的扩展名被拒绝", r2.status_code == 400, f"status={r2.status_code}")


def test_upload_dedup(client):
    content = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64
    r1 = client.post("/api/upload", files=[("files", ("a.png", content, "image/png"))])
    r2 = client.post("/api/upload", files=[("files", ("b.png", content, "image/png"))])
    d1, d2 = r1.json(), r2.json()
    check(
        "同一内容重复上传返回同一 URL（内容去重）",
        r1.status_code == 200 and r2.status_code == 200
        and d1["files"][0]["url"] == d2["files"][0]["url"],
        f"{d1} vs {d2}",
    )
    path = server.UPLOAD_DIR / Path(d1["files"][0]["url"]).name
    check("去重后文件确实落盘", path.is_file(), str(path))
    _UPLOAD_CLEANUP.append(path)


def test_upload_heic_dedup(client):
    """HEIC 转码后按转码结果内容寻址：重复上传同一 HEIC 应去重命中同一 URL，
    且落盘内容是转码后的 JPEG（文件名哈希 = 内容哈希）。"""
    import hashlib
    orig_heic = server._heic_to_jpeg_bytes
    # 固定转码结果，避免依赖真实 pillow_heif/PIL
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x99" * 64  # 假的 JPEG 头 + 填充
    server._heic_to_jpeg_bytes = lambda raw: jpeg_bytes
    # ISO-BMFF 容器：4:8 = 'ftyp'，触发 HEIC 转码分支
    heic = b"\x00\x00\x00\x18" + b"ftyp" + b"heic" + b"\x00" * 16
    try:
        r1 = client.post("/api/upload", files=[("files", ("photo.jpg", heic, "image/jpeg"))])
        r2 = client.post("/api/upload", files=[("files", ("photo2.jpg", heic, "image/jpeg"))])
        d1, d2 = r1.json(), r2.json()
        u1 = d1["files"][0]
        check("HEIC 上传转码后返回 .jpg", r1.status_code == 200 and u1["suffix"] == ".jpg",
              f"status={r1.status_code} data={d1}")
        check("重复上传同一 HEIC 命中同一 URL（去重）",
              r1.status_code == 200 and r2.status_code == 200 and u1["url"] == d2["files"][0]["url"],
              f"{d1} vs {d2}")
        path = server.UPLOAD_DIR / Path(u1["url"]).name
        disk = path.read_bytes() if path.is_file() else b""
        expect_prefix = "att_" + hashlib.sha256(jpeg_bytes).hexdigest()[:12]
        check("落盘内容为转码后 JPEG（文件名哈希=内容哈希）",
              disk == jpeg_bytes and path.name.startswith(expect_prefix) and path.name.endswith(".jpg"),
              f"name={path.name} size={len(disk)}")
        _UPLOAD_CLEANUP.append(path)
    finally:
        server._heic_to_jpeg_bytes = orig_heic


def test_chat_vision(client):
    orig_load = server.load_config
    orig_vision = server._try_vision_chat
    orig_llm = server.llm_chat
    cfg = base_cfg()
    server.load_config = lambda with_env=True: cfg
    calls = []

    async def fake_vision(system, history, req, cfg):
        calls.append(req)
        return "[style:温柔]老婆，这张图我看到了"

    async def fail_llm(*args, **kwargs):
        raise AssertionError("视觉通道成功时不应再调文本模型")

    server._try_vision_chat = fake_vision
    server.llm_chat = fail_llm
    try:
        r = client.post("/api/chat", json={
            "message": "看这张图",
            "attachments": [{"kind": "image", "name": "a.png", "url": "/uploads/a.png"}],
        })
        data = r.json()
        check(
            "图片消息走视觉通道并返回 vision_used",
            r.status_code == 200 and data.get("vision_used") is True and bool(calls),
            f"status={r.status_code} data={data}",
        )
    finally:
        server.load_config = orig_load
        server._try_vision_chat = orig_vision
        server.llm_chat = orig_llm


def test_chat_attachment_fallback(client):
    orig_load = server.load_config
    orig_vision = server._try_vision_chat
    orig_llm = server.llm_chat
    cfg = base_cfg()
    server.load_config = lambda with_env=True: cfg
    calls = []

    async def fake_vision(system, history, req, cfg):
        return None

    async def fake_llm(messages, **kwargs):
        calls.append(messages)
        return "[style:自然]收到你的文档了"

    server._try_vision_chat = fake_vision
    server.llm_chat = fake_llm
    try:
        r = client.post("/api/chat", json={
            "message": "",
            "attachments": [{"kind": "doc", "name": "readme.txt", "url": "/uploads/readme.txt"}],
        })
        data = r.json()
        last_content = calls[0][-1]["content"] if calls else ""
        check(
            "视觉不可用时附件消息回退到文本模型",
            r.status_code == 200 and data.get("vision_used") is False and "用户发送了附件" in last_content,
            f"status={r.status_code} data={data} last_content={last_content}",
        )
    finally:
        server.load_config = orig_load
        server._try_vision_chat = orig_vision
        server.llm_chat = orig_llm


def test_text_doc_prompt():
    txt = "本周会议纪要：周五下午三点复盘训练赛。"
    path = server.UPLOAD_DIR / "att_test_readme.txt"
    path.write_text(txt, encoding="utf-8")
    try:
        prompt = asyncio.run(server._attachment_prompt_text(
            [{"kind": "doc", "name": "readme.txt", "url": f"/uploads/{path.name}"}],
            "帮我看看",
        ))
        check("文本文档内容片段进入提示词", "本周会议纪要" in prompt and "帮我看看" in prompt, prompt)
    finally:
        path.unlink(missing_ok=True)


def main():
    # 访问门禁是后加的，离线测试不测鉴权，临时关掉以免全部 401
    orig_token = server._access_token
    server._access_token = lambda: None
    try:
        with TestClient(server.app) as client:
            test_upload(client)
            test_upload_dedup(client)
            test_upload_heic_dedup(client)
            test_chat_vision(client)
            test_chat_attachment_fallback(client)
            test_text_doc_prompt()
    finally:
        server._access_token = orig_token
    for p in _UPLOAD_CLEANUP:
        for _ in range(5):
            try:
                p.unlink(missing_ok=True)
                break
            except PermissionError:
                time.sleep(0.3)
    print(f"\n{'=' * 50}\n附件测试完成，失败 {_FAIL} 组")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
