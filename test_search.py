# -*- coding: utf-8 -*-
"""联网搜索功能测试。

运行：python test_search.py
"""
import asyncio
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


def test_marker_parse():
    check(
        "英文 search 标记被解析",
        server._extract_search_query("[style:自然][search:成都AG超玩会 大帅]正文") == "成都AG超玩会 大帅",
        server._extract_search_query("[style:自然][search:成都AG超玩会 大帅]正文"),
    )
    check(
        "中文搜索标记被解析",
        server._extract_search_query("【搜索：大帅近况】") == "大帅近况",
        server._extract_search_query("【搜索：大帅近况】"),
    )
    check(
        "无标记返回空",
        server._extract_search_query("今天训练怎么样") == "",
        server._extract_search_query("今天训练怎么样"),
    )
    check(
        "标记从最终回复剥离",
        server._strip_search_markers("[search:大帅]查到了") == "查到了",
        server._strip_search_markers("[search:大帅]查到了"),
    )


def test_ddg_parser():
    sample = """
    <div class="result results_links results_links_deep web-result ">
      <h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=1">标题A</a></h2>
      <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=1">摘要A</a>
    </div>
    <div class="result results_links results_links_deep web-result ">
      <h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fb&amp;rut=2">标题B</a></h2>
      <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fb&amp;rut=2">摘要B</a>
    </div>
    """
    parser = server._DDGResultParser()
    parser.feed(sample)
    parser.close()
    ok = (
        len(parser.results) == 2
        and parser.results[0]["title"] == "标题A"
        and parser.results[0]["snippet"] == "摘要A"
        and parser.results[0]["url"] == "https://example.com/a"
    )
    check("DuckDuckGo 结果页解析标题/摘要/真实链接", ok, str(parser.results))


def test_chat_search_loop():
    orig_llm = server.llm_chat
    orig_search = server.web_search
    llm_calls = []
    search_calls = []

    async def fake_llm(messages, temperature=0.8, max_tokens=768, model=None,
                       disable_thinking=False, thinking=None, anti_repeat=False,
                       cfg=None):
        llm_calls.append(messages)
        if len(llm_calls) == 1:
            return "[style:自然][search:成都AG超玩会 大帅 最新比赛]"
        return "[style:自然]我刚查了一下，最近的消息是……"

    async def fake_search(query, cfg=None, max_results=5):
        search_calls.append(query)
        return [{"title": "大帅近况", "snippet": "最新比赛消息", "url": "https://example.com"}]

    server.llm_chat = fake_llm
    server.web_search = fake_search
    cfg = copy.deepcopy(server.load_config(with_env=False))
    cfg["role_engine"] = {"enabled": False}
    cfg.setdefault("search", {})["enabled"] = True
    try:
        raw, searched = asyncio.run(
            server._chat_with_search(
                {"role": "system", "content": "你是大帅"},
                [],
                "我最近有什么比赛？",
                cfg,
            )
        )
        check(
            "模型输出搜索标记后自动搜索并二次回答",
            searched is True
            and len(llm_calls) == 2
            and search_calls == ["成都AG超玩会 大帅 最新比赛"]
            and "[search:" not in raw,
            f"raw={raw} llm_calls={len(llm_calls)} search_calls={search_calls}",
        )
    finally:
        server.llm_chat = orig_llm
        server.web_search = orig_search


def test_api_search(client):
    orig_search = server.web_search

    async def fake_search(query, cfg=None, max_results=5):
        return [{"title": "大帅", "snippet": "电竞选手", "url": "https://example.com"}]

    server.web_search = fake_search
    try:
        r = client.post("/api/search", json={"query": "大帅"})
        data = r.json()
        check(
            "/api/search 返回搜索结果",
            r.status_code == 200 and data["results"][0]["title"] == "大帅",
            str(data),
        )
    finally:
        server.web_search = orig_search


def test_chat_searched_flag(client):
    orig_load = server.load_config
    orig_chat_search = server._chat_with_search
    cfg = copy.deepcopy(server.load_config(with_env=False))
    cfg["role_engine"] = {"enabled": False}
    cfg["provider"] = "cloud"
    cfg["cloud"] = {"base_url": "http://fake/v1", "model": "fake", "api_key": "none"}
    server.load_config = lambda with_env=True: cfg

    async def fake_chat_search(system, history, user_content, cfg, temperature=0.8, anti_repeat=False,
                               max_tokens=None, thinking=None):
        return "[style:自然]查到了", True

    server._chat_with_search = fake_chat_search
    try:
        r = client.post("/api/chat", json={"message": "我最近有什么比赛？"})
        data = r.json()
        check(
            "/api/chat 返回 searched 标记",
            r.status_code == 200 and data.get("searched") is True and data.get("reply") == "查到了",
            str(data),
        )
    finally:
        server.load_config = orig_load
        server._chat_with_search = orig_chat_search


def main():
    # 访问门禁是后加的，离线测试不测鉴权，临时关掉以免全部 401
    orig_token = server._access_token
    server._access_token = lambda: None
    try:
        with TestClient(server.app) as client:
            test_marker_parse()
            test_ddg_parser()
            test_chat_search_loop()
            test_api_search(client)
            test_chat_searched_flag(client)
    finally:
        server._access_token = orig_token
    print(f"\n{'=' * 50}\n搜索测试完成，失败 {_FAIL} 组")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
