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


def test_story_regex_detect():
    """剧情赛果正则：赢/输+比分要认得出，假设句/无胜负词绝不许误记。"""
    d = server._story_regex_detect
    r = d("今天赢了 3:1，爽")
    check("赛果识别：赢+半角比分", r is not None and r["win"] is True and r["score"] == "3:1", str(r))
    r2 = d("输了1：3，我的问题")
    check("赛果识别：输+全角比分", r2 is not None and r2["win"] is False and r2["score"] == "1:3", str(r2))
    check("赛果识别：无比分不记（交给 LLM）", d("这场赢了，打得不错") is None)
    check("赛果识别：无胜负词不记", d("今天天气不错 3:1") is None)
    check("赛果识别：假设句「要是」不误记", d("要是赢了3:1就请你们吃饭") is None)
    check("赛果识别：假设句「如果」不误记", d("如果输了1:3怎么办") is None)
    check("赛果识别：假设句「差点」不误记", d("差点就赢了 3:1") is None)
    check("赛果识别：「本来能赢」不误记", d("本来能赢 3:1 的，可惜了") is None)
    check("赛果识别：训练赛不误记", d("今天训练赛 3:1 赢了一场") is None)
    check("赛果识别：巅峰赛不误记", d("巅峰赛 2:0 拿下，手感来了") is None)
    check("赛果识别：排位不误记", d("排位三连胜，最后一局 3:1 赢的") is None)
    check("赛果识别：时钟比分不误记", d("今晚 19:00 的比赛我们赢了") is None)
    check("赛果识别：比分越界不记（5:3）", d("我们 5:3 赢了") is None)
    check("赛果识别：平局比分不记（2:2）", d("我们 2:2 打平了") is None)
    check("赛果识别：BO7 大比分 4:3 仍可记",
          (lambda r: r is not None and r["win"] is True and r["score"] == "4:3")(d("总决赛 4:3 赢了，冠军！")))
    check("剧情日期校验：非法格式拒绝", not server._story_date_valid("abc"))
    check("剧情日期校验：越界日期拒绝", not server._story_date_valid("2027-13-99"))
    check("剧情日期校验：合法日期通过", server._story_date_valid("2027-01-14"))


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
                        disable_thinking=False, thinking=None, anti_repeat=False,
                        cfg=None):
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
              test_story_regex_detect, test_time_hint, test_retry_guard):
        t()
    print(f"\n{'=' * 50}\n共 6 组，失败 {_FAIL} 组")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
