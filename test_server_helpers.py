# -*- coding: utf-8 -*-
"""server.py 对话辅助函数单元测试。

运行：python test_server_helpers.py
覆盖：历史去重/规整、机械复读检测、与上一条回复的相似度判断。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os as _os
_os.environ.setdefault("AI_DISABLE_EXTERNAL", "1")  # 直跑本文件也切断后台外部请求（烧 token）
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
    # 动态段现统一由 _assemble_dynamic_hints 组装（chat/greeting 唯一入口，防分叉）
    check("chat走统一入口(新分段)",
          "build_system_content(" in src
          and "_assemble_dynamic_hints(ctx_block, ctx_layers," in src)
    check("greeting走统一入口(新分段)",
          "greeting_instruction" in src
          and src.count("_assemble_dynamic_hints(ctx_block, ctx_layers,") >= 2  # chat + greeting
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
    """token 用量记账：合法 usage 累加，脏值忽略。

    第八轮起记账会节流落盘（data/llm_usage.json）：落盘路径必须重定向到临时目录、
    计数全局在 finally 恢复，绝不能把测试数值写进真实用量统计。"""
    import tempfile
    from pathlib import Path as _P
    before_total = dict(server._llm_usage_total)
    before_by_prov = dict(server._llm_usage_by_provider)
    before_dirty = server._llm_usage_dirty_calls
    before_flush = server._llm_usage_last_flush
    orig_path = server._LLM_USAGE_PATH
    server._LLM_USAGE_PATH = _P(tempfile.mkdtemp(prefix="usage_rec_")) / "llm_usage.json"
    try:
        server._record_llm_usage("mimo", "mimo-v2.5",
                                 {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        server._record_llm_usage("mimo", "mimo-v2.5",
                                 {"prompt_tokens": 10})  # 缺 completion：按 prompt+0 计
        server._record_llm_usage("mimo", "mimo-v2.5", None)
        server._record_llm_usage("mimo", "mimo-v2.5", {"prompt_tokens": "x"})
        check("合法 usage 累加 calls=2",
              server._llm_usage_total["calls"] - before_total["calls"] == 2)
        check("prompt 累计 +110",
              server._llm_usage_total["prompt_tokens"] - before_total["prompt_tokens"] == 110)
        check("completion 累计 +50",
              server._llm_usage_total["completion_tokens"] - before_total["completion_tokens"] == 50)
        check("total 累计 +160",
              server._llm_usage_total["total_tokens"] - before_total["total_tokens"] == 160)
    finally:
        server._llm_usage_total.clear()
        server._llm_usage_total.update(before_total)
        server._llm_usage_by_provider.clear()
        server._llm_usage_by_provider.update(before_by_prov)
        server._llm_usage_dirty_calls = before_dirty
        server._llm_usage_last_flush = before_flush
        server._LLM_USAGE_PATH = orig_path


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


def test_postprocess_gate_integration():
    """端到端：琐碎轮跳过标注 LLM 但亲密度照涨；信号轮照常调用。"""
    import asyncio
    import tempfile
    from pathlib import Path
    import role_engine

    orig_load = server.load_config
    orig_stores = server._get_role_stores
    orig_pp = server._get_post_processor
    cfg = {"active_role": "tg", "role_engine": {"postproc_gate": True}}
    server.load_config = lambda with_env=True: cfg
    td = Path(tempfile.mkdtemp(prefix="gate_test_"))
    mem = role_engine.MemoryStore(td, "tg")
    st = role_engine.StateStore(td, "tg")
    server._get_role_stores = lambda role: (mem, st)
    calls = {"n": 0}

    class FakePP:
        async def run(self, *a, **k):
            calls["n"] += 1
            return {"emotion": {"valence": 0.5, "arousal": 0.4}, "energy_delta": 0.0,
                    "memories": [], "story_result": None, "story_flag": None}

    server._get_post_processor = lambda: FakePP()
    try:
        async def _run():
            await server._post_process_chat("tg", "嗯嗯", "好嘞老公，知道啦")
        asyncio.run(_run())
        check("琐碎轮：标注 LLM 零调用", calls["n"] == 0, f"calls={calls['n']}")
        state = st.get_decayed()
        check("琐碎轮：亲密度仍 +0.01 微涨", abs(state["intimacy"] - 0.31) < 0.005,
              f"intimacy={state['intimacy']}")
        check("琐碎轮：未凭空写记忆", mem.count() == 0, f"count={mem.count()}")

        async def _run2():
            await server._post_process_chat("tg", "我喜欢你", "我也喜欢你老公")
        asyncio.run(_run2())
        check("信号轮：标注 LLM 正常调用", calls["n"] == 1, f"calls={calls['n']}")

        # 配置开关关闭时一律走 LLM（回退旧行为）
        cfg["role_engine"]["postproc_gate"] = False
        async def _run3():
            await server._post_process_chat("tg", "嗯嗯", "好嘞老公，知道啦")
        asyncio.run(_run3())
        check("postproc_gate=false 时不节流", calls["n"] == 2, f"calls={calls['n']}")
    finally:
        server.load_config = orig_load
        server._get_role_stores = orig_stores
        server._get_post_processor = orig_pp


def test_chat_prompt_structure():
    """真实 /api/chat 走查：稳定约定在动态事实之前；时间指令收尾；
    新闻/位置事实全文只出现一次（旧版中段、尾部各一遍）。"""
    import tempfile
    from datetime import datetime
    from pathlib import Path
    from fastapi.testclient import TestClient
    import role_engine

    orig_load = server.load_config
    orig_chat = server.llm_chat
    orig_token = server._access_token
    orig_stores = server._get_role_stores
    orig_news = server._role_news_text
    orig_weather = server._weather_text
    orig_city = server._location_city
    orig_spawn = server._spawn_bg
    cfg = {
        "provider": "cloud", "active_role": "tp", "persona": "测试人设固定内容XYZ",
        "cloud": {"base_url": "http://fake/v1", "model": "fake"},
        "roles": {"tp": {"name": "小测", "persona": "测试人设固定内容XYZ"}},
        "voice": {}, "search": {"enabled": False},
        "role_engine": {"enabled": True, "top_k": 5},
    }
    td = Path(tempfile.mkdtemp(prefix="prompt_struct_"))
    mem = role_engine.MemoryStore(td, "tp")
    st = role_engine.StateStore(td, "tp")
    mem.add("用户喜欢被哄睡", 0.8)
    captured = {}

    async def fake_chat(messages, temperature=0.8, max_tokens=768, model=None,
                        disable_thinking=False, thinking=None, anti_repeat=False, cfg=None):
        captured["sys"] = messages[0]["content"]
        return "[style:自然]好的老公，我在基地呢"

    server.load_config = lambda with_env=True: cfg
    server.llm_chat = fake_chat
    server._access_token = lambda: None
    server._get_role_stores = lambda role: (mem, st)
    server._role_news_text = lambda: "大帅昨天抵达上海备战秋季赛动态全文ABC"
    server._weather_text = lambda: ""
    server._location_city = lambda: "成都市高新区"
    server._spawn_bg = lambda coro: coro.close()  # 丢掉后台后处理，避免无意义调用
    try:
        with TestClient(server.app) as client:
            r = client.post("/api/chat", json={"message": "在干嘛", "history": []})
            check("/api/chat 200", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
        s = captured.get("sys", "")
        check("抓到了 system prompt", bool(s))
        check("动态全文只出现 1 次（旧版 2 次）",
              s.count("大帅昨天抵达上海备战秋季赛动态全文ABC") == 1,
              f"count={s.count('大帅昨天抵达上海备战秋季赛动态全文ABC')}")
        check("位置事实只出现 1 次（旧版 2 次）", s.count("成都市高新区") == 1,
              f"count={s.count('成都市高新区')}")
        today = datetime.now().strftime("%Y-%m-%d")
        check("日期只出现 1 次（旧版 2 次）", s.count(today) == 1, f"count={s.count(today)}")
        i_meta = s.find("【禁止元话语】")
        i_ctx = s.find("【此刻上下文】")
        i_time = s.find("【当前真实时间】")
        i_news = s.find("【你的现实动态】")
        check("稳定约定在上下文块之前（前缀缓存友好）", 0 < i_meta < i_ctx, f"{i_meta} {i_ctx}")
        check("现实动态层在尾部引用指令之前", 0 < i_news < s.find("【你最近的现实动态】"))
        check("时间指令在最尾部（注意力最高）", i_time > i_ctx and i_time > i_news,
              f"{i_time} {i_ctx} {i_news}")
        check("尾部时间指令为引用式", "【此刻上下文】" in s[i_time:])
    finally:
        server.load_config = orig_load
        server.llm_chat = orig_chat
        server._access_token = orig_token
        server._get_role_stores = orig_stores
        server._role_news_text = orig_news
        server._weather_text = orig_weather
        server._location_city = orig_city
        server._spawn_bg = orig_spawn


def test_friendly_llm_http_error():
    """LLM HTTP 错误友好化：402/429/401 返回可操作中文，不把英文状态与原始 body 抛给用户。
    实测来源：MiMo 余额不足时 402，旧逻辑直接弹 '402 Payment Required' + JSON。"""
    m402 = server._friendly_llm_http_error(402, "Insufficient account balance")
    check("402 提示充值或换模型", "余额" in m402 and "切换其他模型" in m402, m402)
    check("402 不出现英文/状态码", "Payment Required" not in m402 and "402" not in m402, m402)
    m429 = server_friendly(429)
    check("429 提示限流稍候", "限流" in m429 and "稍" in m429, m429)
    m401 = server_friendly(401)
    check("401 提示检查 Key", "API Key" in m401, m401)
    m500 = server._friendly_llm_http_error(500, "boom")
    check("未命中映射保留状态码与 body", "500" in m500 and "boom" in m500, m500)


def server_friendly(code):
    return server._friendly_llm_http_error(code, "x")


def main():
    for t in (test_clean_history, test_degenerate_reply, test_too_similar_to_last,
              test_story_regex_detect, test_time_hint, test_retry_guard,
              test_login_rate_limit, test_fix_addressing, test_obedience_core,
              test_hint_dedup, test_postproc_gate, test_usage_recorder,
              test_greeting_singleflight, test_postprocess_gate_integration,
              test_chat_prompt_structure, test_friendly_llm_http_error):
        t()
    print(f"\n{'='*50}\n共 16 组，失败 {_FAIL} 组")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
