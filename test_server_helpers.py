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


def test_login_rate_limit():
    from fastapi.testclient import TestClient

    orig_token = server._access_token
    server._access_token = lambda: "correct-token"
    server._login_track.clear()
    try:
        with TestClient(server.app) as client:
            for _ in range(server._LOGIN_FAIL_MAX):
                r = client.post("/api/login", json={"token": "wrong"})
            check("达到上限的当次失败仍返回 401", r.status_code == 401, f"status={r.status_code}")
            r = client.post("/api/login", json={"token": "wrong"})
            check("连续失败达到上限后返回 429 锁定", r.status_code == 429, f"status={r.status_code}")
            r = client.post("/api/login", json={"token": "correct-token"})
            check("锁定期间正确口令也被拒（429）", r.status_code == 429, f"status={r.status_code}")
            server._login_track.clear()
            r = client.post("/api/login", json={"token": "correct-token"})
            check("无失败记录时正确口令登录成功", r.status_code == 200, f"status={r.status_code}")
    finally:
        server._access_token = orig_token
        server._login_track.clear()


def test_fix_addressing():
    fix = server._fix_addressing
    check("自称老公被纠正", "我是你老婆" in fix("我是你老公"))
    check("电竞术语 carry 保留", "carry型辅助" in fix("他们都叫我carry型辅助"))
    check("小写电竞术语 bp/solo 保留",
          "bp" in fix("这波bp做得不错，晚上solo吗") and "solo" in fix("这波bp做得不错，晚上solo吗"))
    check("普通英文被清理", "hello" not in fix("hello老公，我来了"))
    check("大写赛事术语保留", "KPL" in fix("下一场KPL常规赛") and "MVP" in fix("MVP给谁"))
    check("本宝宝被替换", "本宝宝" not in fix("本宝宝想你了"))


def test_obedience_core():
    out = server.build_system_content("人设正文", "稳定约定块", "动态上下文块")
    check("服从铁律带显式标记", "【服从铁律" in out, out[:120])
    check("分段顺序：人设 < 铁律 < 稳定约定 < 动态上下文",
          out.index("人设正文") < out.index("【服从铁律") < out.index("稳定约定块")
          < out.index("动态上下文块"), out[:200])
    check("稳定段与动态段保留", "稳定约定块" in out and "动态上下文块" in out)
    check("铁律声明最高优先级", "最高优先级" in out or "最高指令" in out)
    check("铁律保留安全底线", "违法" in out)
    check("空值容错", server.build_system_content("", "", "") == server._OBEDIENCE_CORE)
    # 两处组装点必须走统一入口（防分叉），且共用 stable_hints/dynamic_hints 命名
    # （前缀缓存友好重排后，chat 与 greeting 的分段结构不得再分叉）
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.joinpath("server.py").read_text(encoding="utf-8")
    check("chat走统一入口(新分段)",
          "build_system_content(persona, stable_hints, ctx_block + dynamic_hints)" in src)
    check("greeting走统一入口(新分段)",
          "build_system_content(persona, stable_hints,\n                                              greeting_instruction + dynamic_hints)" in src
          and src.count("build_system_content(") >= 3)  # 定义+两处调用
    check("稳定段含元话语约定", "stable_hints = _META_HINT + style_hint" in src)


def test_hint_dedup():
    """尾部指令去重：上下文块已携带事实时只发引用式指令（不复述事实全文）。"""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    full = server._time_hint(False)
    ref = server._time_hint(True)
    check("时间指令(全量)带日期", today in full, full[:80])
    check("时间指令(引用式)不重复日期", today not in ref and "【此刻上下文】" in ref, ref[:80])
    news = "大帅昨天随队抵达上海备战KPL秋季赛，今晚七点首战"
    n_full = server._role_news_hint(news, False)
    n_ref = server._role_news_hint(news, True)
    check("动态指令(全量)复述动态全文", news in n_full)
    check("动态指令(引用式)不复述全文", news not in n_ref and "【你的现实动态】" in n_ref)
    check("引用式指令明显更短（省 token）", len(n_ref) < len(n_full) - len(news) + 40)
    l_full = server._location_hint("成都市高新区", False)
    l_ref = server._location_hint("成都市高新区", True)
    check("位置指令(全量)带地点", "成都市高新区" in l_full)
    check("位置指令(引用式)不带地点", "成都市高新区" not in l_ref and "【用户位置】" in l_ref)
    w_full = server._weather_hint(False, "晴，气温26度")
    w_ref = server._weather_hint(True, "晴，气温26度")
    check("天气指令(全量)带天气文案", "26度" in w_full)
    check("天气指令(引用式)不带天气文案", "26度" not in w_ref and "【今日天气】" in w_ref)
    check("无动态时两种模式都为空", server._role_news_hint("", True) == ""
          and server._role_news_hint("", False) == "")
    check("无位置时两种模式都为空", server._location_hint("", True) == ""
          and server._location_hint("", False) == "")


def test_postproc_gate():
    """后处理 LLM 节流：只对短且无信号的寒暄生效，情绪/剧情/约定类一律放行。"""
    gate = server._is_trivial_exchange
    # 该跳过的纯寒暄（旧行为：每轮都白发一次标注 LLM）
    for u, r in [("嗯嗯", "好嘞老公，知道啦"), ("好的", "行，听你的"),
                 ("哈哈哈", "笑什么呢笨蛋"), ("666", "那必须的"),
                 ("在吗", "在呢老公"), ("[偷笑]", "调皮")]:
        check(f"短寒暄被节流({u!r})", gate(u, r), f"u={u!r} r={r!r}")
    # 绝不能节流：情绪、剧情、约定、偏好、健康/人生大事（哪怕很短）
    for u, r in [("我喜欢你", "我也喜欢你老公"), ("今天好累", "累了就早点休息"),
                 ("我们3:1赢了", "干得漂亮"), ("周末陪我", "好，周末陪你"),
                 ("记得吃饭", "记住了"), ("我发烧了", "严重吗老公"),
                 ("晚安老公爱你", "爱你，晚安"), ("我辞职了", "想清楚就好"),
                 ("想吃火锅", "走，带你去")]:
        check(f"信号轮不节流({u!r})", not gate(u, r), f"u={u!r} r={r!r}")
    # 回复稍长就不节流（可能有情绪展开/事实，宁可多调一次）
    check("长回复不节流", not gate("嗯嗯", "好嘞老公，我跟你说今天训练赛那个事啊真是一言难尽"))
    # 空文本不节流（交给原链路防御逻辑）
    check("空回复不节流", not gate("在吗", "") and not gate("", "在呢"))


def test_usage_recorder():
    """token 用量记账：合法 usage 累加，脏值忽略。"""
    before = dict(server._llm_usage_total)
    server._record_llm_usage("mimo", "mimo-v2.5",
                             {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
    server._record_llm_usage("mimo", "mimo-v2.5",
                             {"prompt_tokens": 10})  # 缺 completion：按 prompt+0 计
    server._record_llm_usage("mimo", "mimo-v2.5", None)
    server._record_llm_usage("mimo", "mimo-v2.5", {"prompt_tokens": "x"})
    check("合法 usage 累加 calls=2", server._llm_usage_total["calls"] - before["calls"] == 2)
    check("prompt 累计 +110", server._llm_usage_total["prompt_tokens"] - before["prompt_tokens"] == 110)
    check("completion 累计 +50", server._llm_usage_total["completion_tokens"] - before["completion_tokens"] == 50)
    check("total 累计 +160", server._llm_usage_total["total_tokens"] - before["total_tokens"] == 160)


def test_greeting_singleflight():
    """同角色并发问候只允许一次 LLM 调用（旧行为：两个请求各烧一次完整调用）。"""
    import asyncio
    import threading
    from fastapi.testclient import TestClient

    orig_load = server.load_config
    orig_chat = server.llm_chat
    orig_token = server._access_token
    cfg = {
        "provider": "cloud", "active_role": "trole", "persona": "测试人设",
        "cloud": {"base_url": "http://fake/v1", "model": "fake"},
        "roles": {"trole": {"name": "小测"}},
        "voice": {}, "role_engine": {"enabled": False},
    }
    server.load_config = lambda with_env=True: cfg
    calls = {"n": 0}

    async def fake_chat(messages, temperature=0.8, max_tokens=768, model=None,
                        disable_thinking=False, thinking=None, anti_repeat=False, cfg=None):
        calls["n"] += 1
        await asyncio.sleep(0.3)  # 放大窗口，保证第二个请求在生成期间进入
        return "[style:自然]老公，在忙吗"

    server.llm_chat = fake_chat
    server._access_token = lambda: None
    server._greeting_inflight.clear()
    results: list = []
    errors: list = []
    try:
        with TestClient(server.app) as client:
            def _hit():
                try:
                    r = client.post("/api/greeting")
                    results.append((r.status_code, r.json().get("reply")))
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            t1 = threading.Thread(target=_hit)
            t2 = threading.Thread(target=_hit)
            t1.start(); t2.start()
            t1.join(5); t2.join(5)
        check("并发问候无异常", not errors, str(errors[:1]))
        check("两个请求都 200 且拿到同一回复",
              len(results) == 2 and all(code == 200 and reply == "老公，在忙吗" for code, reply in results),
              str(results))
        check("并发合并：LLM 只调用 1 次（旧行为为 2 次）", calls["n"] == 1, f"calls={calls['n']}")
        check("完成后在飞表已清理", not server._greeting_inflight)
    finally:
        server.load_config = orig_load
        server.llm_chat = orig_chat
        server._access_token = orig_token
        server._greeting_inflight.clear()


def main():
    for t in (test_clean_history, test_degenerate_reply, test_too_similar_to_last,
              test_story_regex_detect, test_time_hint, test_retry_guard,
              test_login_rate_limit, test_fix_addressing, test_obedience_core,
              test_hint_dedup, test_postproc_gate, test_usage_recorder,
              test_greeting_singleflight):
        t()
    print(f"\n{'='*50}\n共 13 组，失败 {_FAIL} 组")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
