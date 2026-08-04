# -*- coding: utf-8 -*-
"""server.py 对话辅助函数单元测试。

运行：python test_server_helpers.py
覆盖：历史去重/规整、机械复读检测、与上一条回复的相似度判断。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import server
from server import _clean_history, _is_degenerate_reply, _too_similar_to_last

_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


def test_clean_history():
    hist = [
        {"role": "user", "content": "在吗"},
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "[style:温柔]在呢"},
        {"role": "user", "content": "想你了"},
        {"role": "user", "content": "想你了"},
    ]
    out = _clean_history(hist, "想你了")
    expected = [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "在呢"},
    ]
    check("连续重复消息折叠，当前消息只留一份", out == expected, str(out))
    check("旧回复里的 style 标记被剥离", out[1]["content"] == "在呢", str(out))


def test_degenerate_reply():
    check("正常回复不算复读", not _is_degenerate_reply("今天训练不错，晚上一起吃饭吧"))
    check("单字循环被识别", _is_degenerate_reply("[style:俏皮]" + "最" * 80))
    check("空回复被识别", _is_degenerate_reply(""))


def test_too_similar_to_last():
    prev = "老公你这耳朵可真是我的命根子，我这副身子骨全靠你养着呢。"
    check("与上一条回复完全相同判为复读",
          _too_similar_to_last(prev, [{"role": "assistant", "content": prev}]))
    check("话题变化不算复读",
          not _too_similar_to_last("今天训练赛赢了，咱们晚上吃什么？",
                                   [{"role": "assistant", "content": prev}]))


def test_time_hint():
    from datetime import datetime
    hint = server._time_hint()
    today = datetime.now().strftime("%Y-%m-%d")
    check("时间指令包含当前真实日期", today in hint, hint[:80])
    check("时间指令带显式标记", "【当前真实时间】" in hint)
    check("时间指令禁止编造时间", "禁止" in hint and "编造" in hint)
    anchored = server._time_anchor("现在几点了")
    check("时间锚点前置且保留原消息",
          anchored.startswith("(系统提供的当前真实时间：") and anchored.endswith("现在几点了"),
          anchored[:80])
    check("时间锚点包含当前真实日期", today in anchored)


def test_retry_guard():
    import copy
    from fastapi.testclient import TestClient

    orig_load = server.load_config
    orig_chat = server.llm_chat
    cfg = copy.deepcopy(server.load_config(with_env=False))
    cfg["provider"] = "cloud"
    cfg.setdefault("cloud", {})["base_url"] = "http://fake/v1"
    cfg["cloud"]["model"] = "fake"
    cfg.setdefault("role_engine", {})["enabled"] = False
    server.load_config = lambda with_env=True: cfg

    calls = []

    async def fake_chat(messages, temperature=0.8, max_tokens=768, model=None,
                        disable_thinking=False, thinking=None, anti_repeat=False):
        calls.append((temperature, anti_repeat))
        if len(calls) == 1:
            return "[style:自然]老公你这耳朵可真是我的命根子，我这副身子骨全靠你养着呢。"
        return "[style:自然]好的，这次换个说法。"

    server.llm_chat = fake_chat
    # 访问门禁是后加的，离线测试不测鉴权，临时关掉以免 401
    orig_token = server._access_token
    server._access_token = lambda: None
    try:
        with TestClient(server.app) as client:
            r = client.post("/api/chat", json={
                "message": "你怎么又重复了",
                "history": [{"role": "assistant", "content": "老公你这耳朵可真是我的命根子，我这副身子骨全靠你养着呢。"}],
            })
            data = r.json()
            check("复读时自动重试并返回新回复",
                  len(calls) == 2 and data.get("reply") == "好的，这次换个说法。",
                  f"calls={calls} reply={data.get('reply')}")
    finally:
        server.load_config = orig_load
        server.llm_chat = orig_chat
        server._access_token = orig_token


def main():
    for t in (test_clean_history, test_degenerate_reply, test_too_similar_to_last,
              test_time_hint, test_retry_guard):
        t()
    print(f"\n{'=' * 50}\n共 5 组，失败 {_FAIL} 组")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
