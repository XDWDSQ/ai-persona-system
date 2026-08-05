# -*- coding: utf-8 -*-
"""MiniMax 云端文字生成适配器单元测试。

运行：python test_minimax_llm.py
覆盖：配置提取（计费模式优先级）、payload 构造（含流式计费参数）、usage 解析、
     错误码映射（1008 余额不足 / 2056 Token Plan 超限 / 鉴权）、chat/chat_stream
     成功与失败路径、Token Plan 配额查询、server.py 配置合并接入。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import minimax_llm
from minimax_llm import (LLMUsage, MiniMaxConf, build_payload, chat, chat_stream,
                         map_error, parse_usage, query_quota, usage_total)

_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


# ---------------------------------------------------------------- 配置 --------
def test_conf_from_cfg():
    c = MiniMaxConf.from_cfg({})
    check("空配置回落默认 base_url/model/payg",
          c.base_url == "https://api.minimaxi.com/v1" and c.model == "MiniMax-M2.5"
          and c.billing_mode == "payg",
          f"base={c.base_url} model={c.model} mode={c.billing_mode}")
    c = MiniMaxConf.from_cfg({"billing_mode": "token_plan", "api_key": "sk-tp"})
    check("config 指定 token_plan 生效", c.billing_mode == "token_plan")
    c = MiniMaxConf.from_cfg({}, env={"MINIMAX_BILLING_MODE": "token_plan"})
    check("env 兜底 token_plan", c.billing_mode == "token_plan")
    c = MiniMaxConf.from_cfg({"billing_mode": "token_plan"}, env={"MINIMAX_BILLING_MODE": "payg"})
    check("config 优先于 env", c.billing_mode == "token_plan")
    c = MiniMaxConf.from_cfg({"billing_mode": "hack"})
    check("非法计费模式回退 payg", c.billing_mode == "payg")
    c = MiniMaxConf.from_cfg({"base_url": "https://api.minimaxi.com/v1/", "quota_url": "https://q.x/v1/token_plan/remains"})
    check("base_url 尾斜杠被去除", c.base_url == "https://api.minimaxi.com/v1")
    check("quota 端点同域名拼接",
          c.quota_endpoint() == "https://api.minimaxi.com/v1/token_plan/remains",
          c.quota_endpoint())
    c = MiniMaxConf.from_cfg({"base_url": "https://gateway.example.com/v1", "quota_url": "https://www.minimaxi.com/v1/token_plan/remains"})
    check("非 api.* 域名用配置的 quota_url", c.quota_endpoint().startswith("https://www.minimaxi.com"),
          c.quota_endpoint())


# ---------------------------------------------------------------- payload -----
def test_build_payload():
    msgs = [{"role": "user", "content": "你好"}]
    p = build_payload(msgs, "MiniMax-M2.5", 0.8, 768, True, stream=False)
    check("非流式不带 stream 字段", "stream" not in p)
    check("thinking 开启", p.get("thinking") == {"type": "enabled"})
    p = build_payload(msgs, "MiniMax-M2.5", 0.8, 768, False, stream=True)
    check("流式带 stream=true", p.get("stream") is True)
    check("流式带标准计费参数 include_usage", p.get("stream_options") == {"include_usage": True},
          str(p.get("stream_options")))
    check("thinking 关闭", p.get("thinking") == {"type": "disabled"})
    check("基础字段齐全",
          p["model"] == "MiniMax-M2.5" and p["temperature"] == 0.8 and p["max_tokens"] == 768)


# ---------------------------------------------------------------- usage -------
def test_parse_usage():
    u = parse_usage({"usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})
    check("标准 usage 解析", u == LLMUsage(100, 50, 150), str(u))
    u = parse_usage({"base_resp": {"status_code": 0}, "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})
    check("base_resp 包装不影响 usage", u == LLMUsage(7, 3, 10), str(u))
    u = parse_usage({})
    check("缺 usage 返回全 0", u == LLMUsage(), str(u))
    u = parse_usage({"usage": {"prompt_tokens": "5", "completion_tokens": None}})
    check("脏类型容错", u == LLMUsage(5, 0, 0), str(u))
    before = usage_total()["total_tokens"]
    minimax_llm._accumulate_usage(LLMUsage(1, 2, 3))
    check("usage 进程内累计", usage_total()["total_tokens"] == before + 3,
          f"{before} -> {usage_total()['total_tokens']}")


# ---------------------------------------------------------------- 错误映射 ----
def test_map_error():
    e = map_error(200, "", {"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}}, "payg")
    check("1008 余额不足 -> 502 且带计费模式",
          e.status_code == 502 and "余额不足" in e.detail and "payg" in e.detail, e.detail)
    e = map_error(200, "", {"base_resp": {"status_code": 2056}}, "token_plan")
    check("2056 Token Plan 超限 -> 明确提示",
          e.status_code == 502 and "Token Plan" in e.detail, e.detail)
    e = map_error(401, "", {"base_resp": {"status_code": 1004}}, "payg")
    check("1004 鉴权失败", e.status_code == 502 and "混用" in e.detail, e.detail)
    e = map_error(402, "payment required", {}, "payg")
    check("HTTP 402 余额不足兜底", e.status_code == 502 and "余额不足" in e.detail, e.detail)
    e = map_error(429, "rate limited", {}, "payg")
    check("HTTP 429 限流提示", e.status_code == 502 and "频率" in e.detail, e.detail)
    e = map_error(500, "boom", {}, "payg")
    check("HTTP 500 服务错误", e.status_code == 502 and "500" in e.detail, e.detail)
    e = map_error(200, '{"base_resp":{"status_code":1039}}', {}, "payg")
    check("body 文本兜底识别 1039", e.status_code == 502 and "max_tokens" in e.detail, e.detail)


# ---------------------------------------------------------------- fake 客户端 --
class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text or (json_data if isinstance(json_data, str) else "")

    def raise_for_status(self):
        if self.status_code >= 400:
            from httpx import HTTPStatusError
            raise HTTPStatusError(f"HTTP {self.status_code}", request=None, response=self)

    def json(self):
        if isinstance(self._json, str):
            import json as _json
            return _json.loads(self._json)
        return self._json

    async def aread(self):
        return self.text.encode()


class FakeClient:
    """可编程 httpx.AsyncClient 替身：post/stream/get 按脚本返回。"""

    def __init__(self, post_script=None, stream_lines=None, get_response=None):
        self.post_script = post_script or []
        self.stream_lines = stream_lines or []
        self.get_response = get_response
        self.post_calls = []
        self.stream_calls = []
        self.get_calls = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.post_calls.append((url, json, headers))
        if not self.post_script:
            return FakeResponse(200, {"choices": [{"message": {"content": "你好"}}],
                                      "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})
        item = self.post_script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def get(self, url, headers=None, timeout=None):
        self.get_calls.append((url, headers))
        if self.get_response is None:
            return FakeResponse(200, {"data": [{"id": "MiniMax-M2.5"}, {"id": "speech-2.8-hd"}]})
        if isinstance(self.get_response, Exception):
            raise self.get_response
        return self.get_response

    def stream(self, method, url, json=None, headers=None, timeout=None):
        self.stream_calls.append((url, json, headers))
        return _FakeStreamCtx(self.stream_lines)


class _FakeStreamCtx:
    def __init__(self, lines):
        self.lines = lines
        self.status_code = 200
        self.text = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aread(self):
        return b""

    async def aiter_lines(self):
        for ln in self.lines:
            yield ln


def _default_conf(**kw):
    base = {"base_url": "https://api.minimaxi.com/v1", "api_key": "sk-test", "model": "MiniMax-M2.5"}
    base.update(kw)
    return MiniMaxConf.from_cfg(base)


# ---------------------------------------------------------------- chat --------
def test_chat_ok():
    async def run():
        fake = FakeClient()
        content, usage = await chat(_default_conf(), [{"role": "user", "content": "hi"}],
                                    client=fake)
        check("chat 成功返回正文", content == "你好", content)
        check("chat 返回 usage", usage == LLMUsage(3, 2, 5), str(usage))
        check("请求头带 Bearer", fake.post_calls[0][2]["Authorization"] == "Bearer sk-test")
    import asyncio
    asyncio.run(run())


def test_chat_insufficient_balance_no_retry():
    async def run():
        from fastapi import HTTPException
        fake = FakeClient(post_script=[
            FakeResponse(200, {"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}}),
            FakeResponse(200, {"choices": [{"message": {"content": "不应重试"}}]}),
        ])
        try:
            await chat(_default_conf(billing_mode="token_plan"), [{"role": "user", "content": "hi"}], client=fake)
            check("1008 应抛错", False)
        except HTTPException as e:
            check("1008 抛 502 且不重试", e.status_code == 502 and "余额不足" in e.detail
                  and len(fake.post_calls) == 1, f"calls={len(fake.post_calls)} {e.detail}")
    import asyncio
    asyncio.run(run())


def test_chat_empty_retry_then_ok():
    async def run():
        fake = FakeClient(post_script=[
            FakeResponse(200, {"choices": [{"message": {"content": ""}}], "usage": {"total_tokens": 0}}),
            FakeResponse(200, {"choices": [{"message": {"content": "第二次成功"}}],
                                "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}}),
        ])
        content, usage = await chat(_default_conf(), [{"role": "user", "content": "hi"}], client=fake)
        check("空正文自动重试并成功", content == "第二次成功" and len(fake.post_calls) == 2,
              f"calls={len(fake.post_calls)}")
        check("重试后 usage 正确", usage == LLMUsage(4, 6, 10), str(usage))
    import asyncio
    asyncio.run(run())


def test_chat_thinking_downgrade_on_2013():
    async def run():
        fake = FakeClient(post_script=[
            FakeResponse(400, {"base_resp": {"status_code": 2013, "status_msg": "param error"}}),
            FakeResponse(200, {"choices": [{"message": {"content": "无 thinking 成功"}}]}),
        ])
        content, _ = await chat(_default_conf(), [{"role": "user", "content": "hi"}], client=fake)
        check("2013 时去掉 thinking 重试成功", content == "无 thinking 成功", content)
        second_payload = fake.post_calls[1][1]
        check("重试请求不再带 thinking", "thinking" not in second_payload, str(second_payload))
    import asyncio
    asyncio.run(run())


# ---------------------------------------------------------------- stream ------
def _sse_chunks(items):
    return ["data: " + item if item != "[DONE]" else "data: [DONE]" for item in items]


def test_chat_stream_ok_with_usage():
    async def run():
        lines = _sse_chunks([
            '{"choices":[{"delta":{"content":"你"}}]}',
            '{"choices":[{"delta":{"content":"好"}}]}',
            '{"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}',
            "[DONE]",
        ])
        fake = FakeClient(stream_lines=lines)
        got = [p async for p in chat_stream(_default_conf(), [{"role": "user", "content": "hi"}], client=fake)]
        check("流式逐段输出", got == ["你", "好"], str(got))
        check("流式请求带 include_usage",
              fake.stream_calls[0][1].get("stream_options") == {"include_usage": True})
        check("流式末尾 usage 已累计", usage_total()["total_tokens"] >= 5, str(usage_total()))
    import asyncio
    asyncio.run(run())


def test_chat_stream_error_midway():
    async def run():
        from fastapi import HTTPException
        lines = _sse_chunks([
            '{"choices":[{"delta":{"content":"部分"}}]}',
            '{"base_resp":{"status_code":2056,"status_msg":"exceed token plan limit"}}',
        ])
        fake = FakeClient(stream_lines=lines)
        out = []
        try:
            async for p in chat_stream(_default_conf(billing_mode="token_plan"),
                                       [{"role": "user", "content": "hi"}], client=fake):
                out.append(p)
            check("中途报错应抛异常", False, str(out))
        except HTTPException as e:
            check("流式中途 Token Plan 超限报错", e.status_code == 502 and "Token Plan" in e.detail
                  and out == ["部分"], f"out={out} {e.detail}")
    import asyncio
    asyncio.run(run())


# ---------------------------------------------------------------- quota -------
def test_query_quota_payg():
    async def run():
        fake = FakeClient()
        r = await query_quota(_default_conf(billing_mode="payg"), client=fake)
        check("payg 模式返回按量提示", r["ok"] is False and "payg" in r["data"] and len(fake.get_calls) == 0,
              str(r))
    import asyncio
    asyncio.run(run())


def test_query_quota_token_plan():
    async def run():
        fake = FakeClient(get_response=FakeResponse(200, {"total_quota": 1000, "used_quota": 200}))
        r = await query_quota(_default_conf(billing_mode="token_plan"), client=fake)
        check("token_plan 配额查询成功", r["ok"] is True and r["data"]["total_quota"] == 1000, str(r))
        check("配额请求带订阅 Key", fake.get_calls[0][1]["Authorization"] == "Bearer sk-test")
    import asyncio
    asyncio.run(run())


def test_query_quota_no_key():
    async def run():
        r = await query_quota(_default_conf(billing_mode="token_plan", api_key=""), client=FakeClient())
        check("缺订阅 Key 明确提示", r["ok"] is False and "订阅 Key" in r["data"], str(r))
    import asyncio
    asyncio.run(run())


# ---------------------------------------------------------------- server 接入 --
def test_server_merge_billing_mode():
    import copy
    import server
    cfg = copy.deepcopy(server.load_config(with_env=False))
    upd = server.ConfigUpdate(cloud_provider="minimax", cloud_billing_mode="token_plan")
    server._merge_config_update(cfg, upd)
    entry = cfg["cloud_providers"].get("minimax") or {}
    check("billing_mode 合并进 cloud", cfg["cloud"]["billing_mode"] == "token_plan", str(cfg.get("cloud")))
    check("billing_mode 合并进供应商条目", entry.get("billing_mode") == "token_plan", str(entry))
    # 非法值不落盘
    cfg2 = copy.deepcopy(server.load_config(with_env=False))
    server._merge_config_update(cfg2, server.ConfigUpdate(cloud_billing_mode="hack"))
    check("非法计费模式不写入", "billing_mode" not in cfg2.get("cloud", {}), str(cfg2.get("cloud")))


def test_status_includes_minimax():
    import copy
    import server
    cfg = copy.deepcopy(server.load_config(with_env=False))
    providers = cfg.get("cloud_providers", {})
    check("cloud_providers 含 minimax 条目",
          "minimax" in providers and providers["minimax"].get("label") == "MiniMax",
          str(list(providers.keys())))


def main():
    for t in (test_conf_from_cfg, test_build_payload, test_parse_usage, test_map_error,
              test_chat_ok, test_chat_insufficient_balance_no_retry, test_chat_empty_retry_then_ok,
              test_chat_thinking_downgrade_on_2013, test_chat_stream_ok_with_usage,
              test_chat_stream_error_midway, test_query_quota_payg, test_query_quota_token_plan,
              test_query_quota_no_key, test_server_merge_billing_mode, test_status_includes_minimax):
        t()
    print(f"\n{'=' * 50}\n共 15 组，失败 {_FAIL} 组")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
