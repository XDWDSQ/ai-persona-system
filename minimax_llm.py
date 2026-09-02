# -*- coding: utf-8 -*-
"""MiniMax 云端文字生成适配器（OpenAI 兼容协议，与 mimoo/deepseek 等同一套调用链）。

设计目标：调用方（server.py 的 llm_chat / llm_chat_stream / llm_models）无需感知
供应商差异，切换 provider 只改 config.json 的 cloud.provider / cloud_providers 条目。

计费双模式（两者密钥互不混用，见 platform.minimaxi.com Token Plan 文档）：
- payg（按量付费，默认）：Authorization 携带普通 API Key，按实际 token 用量实时计费，
  响应 usage 字段（prompt_tokens / completion_tokens / total_tokens）即本次用量；
- token_plan（Token Plan）：Authorization 携带订阅 Key，扣减套餐额度/已购积分，
  支持 /v1/token_plan/remains 配额查询，余额不足（1008）/ 超限（2056）时给出明确错误。

错误语义与现有云端供应商一致：鉴权失败 / 余额不足 → HTTPException(502)，
可重试错误（5xx/限流/临时错误码）内置指数退避重试；空正文重试并自动降级思考模式。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import httpx
from fastapi import HTTPException

_log = logging.getLogger("minimax_llm")

# ---------------------------------------------------------------- 常量 --------
MINIMAX_DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"
# Token Plan 配额查询官方端点（订阅 Key 调用）；base_url 配了 api.* 域名时优先用同域名拼
MINIMAX_DEFAULT_QUOTA_URL = "https://www.minimaxi.com/v1/token_plan/remains"
MINIMAX_DEFAULT_MODEL = "MiniMax-M3"  # 与 config 默认模型保持一致（无配置时的兜底值）
# 计费模式环境变量兜底：config.json 未配置 billing_mode 时读取（payg | token_plan）
MINIMAX_BILLING_MODE_ENV = "MINIMAX_BILLING_MODE"
# 输出 token 上限（云端给到官方最大值，长文请求不会被自家上限卡住）
MINIMAX_MAX_OUTPUT_TOKENS = 65536

# 重试间的指数退避（与 server.py _LLM_RETRY_BACKOFF 一致），避免瞬时重试加重对端限流
_LLM_RETRY_BACKOFF = (0.5, 1.5)

# M3 思考模式（thinking=adaptive）会先输出一段长推理再出正文；
# 默认 768 max_tokens 会被思考吃光导致正文为空（finish=length），
# 开启思考时把输出预算至少放大到该值，保证「思考 + 正文」都能完整输出。
_THINKING_MIN_TOKENS = 4096

# MiniMax base_resp.status_code 错误码 → (是否可重试, 提示模板)
# 官方错误码表：platform.minimaxi.com/docs/api-reference/errorcode
_MINIMAX_ERROR_CODES = {
    # 鉴权类：Key 无效 / 类型用错（按量 Key 与订阅 Key 混用最常见）
    1004: (False, "MiniMax 鉴权失败（1004）：请检查 API Key；注意按量计费 Key 与 Token Plan 订阅 Key 不可混用"),
    2049: (False, "MiniMax 鉴权失败（2049）：无效的 API Key，请到平台重新生成"),
    # 计费类：余额不足 / Token Plan 超限
    1008: (False, "MiniMax 余额不足（1008）：请检查账户余额（按量付费）或套餐额度/积分（Token Plan）"),
    2056: (False, "超出 MiniMax Token Plan 资源限制（2056）：请等待额度窗口重置，或升级套餐、购买积分"),
    # 临时错误：可重试
    1000: (True, "MiniMax 未知错误（1000）：请稍后重试"),
    1001: (True, "MiniMax 请求超时（1001）：请稍后重试"),
    1024: (True, "MiniMax 内部错误（1024）：请稍后重试"),
    1033: (True, "MiniMax 系统错误（1033）：请稍后重试"),
    1002: (True, "MiniMax 请求频率超限（1002）：请降低调用频率后重试"),
    # 参数/内容类：不重试
    1039: (False, "MiniMax Token 限制（1039）：请减小 max_tokens 后重试"),
    2013: (False, "MiniMax 参数错误（2013）：请检查请求参数与模型 ID"),
    1026: (False, "MiniMax 输入内容涉敏（1026）：请调整输入内容"),
    1027: (False, "MiniMax 输出内容涉敏（1027）：请调整提示词后重试"),
}
# 未登记的错误码按 HTTP 状态码兜底判断是否可重试
_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------- 类型 --------
@dataclass
class LLMUsage:
    """一次 LLM 调用的 token 用量（OpenAI 兼容 usage 字段）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __str__(self) -> str:
        return f"prompt={self.prompt_tokens} completion={self.completion_tokens} total={self.total_tokens}"


@dataclass
class MiniMaxConf:
    """从 config.cloud_providers.minimax / config.cloud 提取的 MiniMax 连接配置。

    billing_mode 取值：payg（按量付费）| token_plan（Token Plan）。
    优先级：config 条目 > 环境变量 MINIMAX_BILLING_MODE > 默认 payg。
    """

    base_url: str = MINIMAX_DEFAULT_BASE_URL
    api_key: str = ""
    model: str = MINIMAX_DEFAULT_MODEL
    billing_mode: str = "payg"
    thinking: bool = True
    quota_url: str = MINIMAX_DEFAULT_QUOTA_URL

    @classmethod
    def from_cfg(cls, conf: dict, env: dict | None = None) -> "MiniMaxConf":
        conf = conf or {}
        env = os.environ if env is None else env
        billing = str(conf.get("billing_mode") or env.get(MINIMAX_BILLING_MODE_ENV) or "payg").strip().lower()
        if billing not in ("payg", "token_plan"):
            billing = "payg"
        return cls(
            base_url=(conf.get("base_url") or MINIMAX_DEFAULT_BASE_URL).rstrip("/"),
            api_key=conf.get("api_key") or "",
            model=conf.get("model") or MINIMAX_DEFAULT_MODEL,
            billing_mode=billing,
            thinking=bool(conf.get("thinking", True)),
            quota_url=(conf.get("quota_url") or MINIMAX_DEFAULT_QUOTA_URL).rstrip("/"),
        )

    def quota_endpoint(self) -> str:
        """Token Plan 配额查询地址：base_url 是 api.* 域名时优先用同域名拼官方路径。"""
        if self.base_url and "api." in self.base_url and "/v1" in self.base_url:
            return self.base_url.replace("/v1", "/v1/token_plan/remains", 1)
        return self.quota_url


# ---------------------------------------------------------------- 辅助 --------
def _timeout(max_tokens: int) -> httpx.Timeout:
    """与 server.py 相同的分阶段超时：connect/write 短，read 按输出上限放宽。"""
    read_to = min(900.0, max(180.0, 120.0 + max_tokens * 0.04))
    return httpx.Timeout(5.0, connect=5.0, write=10.0, read=read_to, pool=15.0)


def _extract_base_resp(data: dict) -> dict:
    """OpenAI 兼容接口偶发带 MiniMax 原生 base_resp 包装（status_code=0 表示成功）。"""
    br = data.get("base_resp") if isinstance(data, dict) else None
    return br if isinstance(br, dict) else {}


def _code_from_error(status_code: int, body: str, data: dict) -> Optional[int]:
    """从响应体提取 MiniMax 错误码：优先 base_resp.status_code，其次 body 文本里查找。"""
    br = _extract_base_resp(data)
    code = br.get("status_code")
    if isinstance(code, int) and code:
        return code
    for c in _MINIMAX_ERROR_CODES:
        if f"{c}" in (body or ""):
            return c
    return None


def map_error(status_code: int, body: str, data: dict, billing_mode: str) -> HTTPException:
    """把 MiniMax 错误响应映射为与现有供应商一致的 HTTPException(502)，中文提示。"""
    code = _code_from_error(status_code, body, data)
    if code is None:
        if status_code == 401:
            return HTTPException(502, "MiniMax 鉴权失败：请检查 API Key；注意按量计费 Key 与 Token Plan 订阅 Key 不可混用")
        if status_code == 402:
            return HTTPException(502, "MiniMax 账户余额不足：请检查账户余额（按量付费）或 Token Plan 额度/积分")
        if status_code == 429:
            return HTTPException(502, "MiniMax 请求频率超限（429）：请降低调用频率后重试")
        if status_code >= 500:
            return HTTPException(502, f"MiniMax 服务错误（HTTP {status_code}）：请稍后重试")
        return HTTPException(502, f"MiniMax 调用失败（HTTP {status_code}）: {body[:300]}")
    retryable, tmpl = _MINIMAX_ERROR_CODES.get(code, (False, f"MiniMax 错误 {code}"))
    return HTTPException(502, tmpl + (f"（计费模式: {billing_mode}）" if code in (1008, 2056) else ""))


def build_payload(messages: list[dict], model: str, temperature: float, max_tokens: int,
                  use_thinking: bool, stream: bool = False) -> dict:
    """构造 OpenAI 兼容请求体。

    - 计费：普通付费模式无需额外参数（服务端按 key 自动计费），流式请求带
      stream_options.include_usage 让最后一个 chunk 返回 usage，便于解析本次用量；
    - 思考：官方取值 adaptive（开启）/ disabled（跳过思考直接回答），仅 MiniMax-M3
      支持关闭（M2.x 接受参数但仍保持开启，由 reasoning_split + <think> 剥离兜底）；
    - reasoning_split：把 thinking 内容拆分到 reasoning_content 字段，正文保持干净
      （M2.x 不拆分会把思考内嵌在 content 的 <think> 标签里）。
    """
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "reasoning_split": True,
    }
    if stream:
        payload["stream"] = True
        # OpenAI 标准计费参数：流式结束 chunk 携带 usage（prompt/completion/total tokens）
        payload["stream_options"] = {"include_usage": True}
    if use_thinking is not None:
        payload["thinking"] = {"type": "adaptive" if use_thinking else "disabled"}
    return payload


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def strip_think(text: str) -> tuple[str, str]:
    """剥离 content 里的 <think>...</think> 块（M2.x 未拆分时思考内嵌在此），
    返回 (正文, 思考内容)。带标签但标签不闭合时整体视作思考（保险处理）。"""
    if not text:
        return "", ""
    if "<think" not in text.lower():
        return text.strip(), ""
    parts: list[str] = []
    think_parts: list[str] = []
    pos = 0
    for m in _THINK_RE.finditer(text):
        parts.append(text[pos:m.start()])
        think_parts.append(m.group(0)[len(_THINK_OPEN):-len(_THINK_CLOSE)].strip())
        pos = m.end()
    parts.append(text[pos:])
    content = "".join(parts).strip()
    if not think_parts and content.lower().startswith(_THINK_OPEN):
        # 只有开标签没有闭标签：整段视为思考（异常响应兜底）
        return "", text[len(_THINK_OPEN):].strip()
    return content, " ".join(t for t in think_parts if t).strip()


def _stream_split_think(piece: str, in_think: bool, think_buf: list[str],
                        on_text) -> bool:
    """流式处理单个 content delta：维护 <think> 跨 chunk 状态。

    返回新的 in_think；正文部分回调 on_text；think 内容累计到 think_buf。"""
    while piece:
        if in_think:
            idx = piece.lower().find(_THINK_CLOSE)
            if idx < 0:
                think_buf.append(piece)
                return True
            think_buf.append(piece[:idx])
            piece = piece[idx + len(_THINK_CLOSE):]
            in_think = False
            continue
        idx = piece.lower().find(_THINK_OPEN)
        if idx < 0:
            on_text(piece)
            return False
        before = piece[:idx]
        if before:
            on_text(before)
        piece = piece[idx + len(_THINK_OPEN):]
        in_think = True
    return in_think


def parse_usage(data: dict) -> LLMUsage:
    """从响应体解析用量（OpenAI 兼容 usage 字段），缺失时返回全 0。

    非流式：data.usage；流式：最后一个无 choices 的 chunk 携带 usage。
    MiniMax 响应可能带 base_resp 包装，不影响 usage 字段位置。
    """
    u = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(u, dict):
        return LLMUsage()
    return LLMUsage(
        prompt_tokens=int(u.get("prompt_tokens") or 0),
        completion_tokens=int(u.get("completion_tokens") or 0),
        total_tokens=int(u.get("total_tokens") or 0),
    )


# 进程内用量累计（便于 /api/llm-quota 与日志查看整体消耗趋势；仅内存，不持久化）
_USAGE_TOTAL: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _accumulate_usage(u: LLMUsage) -> None:
    if not u.total_tokens:
        return
    _USAGE_TOTAL["prompt_tokens"] += u.prompt_tokens
    _USAGE_TOTAL["completion_tokens"] += u.completion_tokens
    _USAGE_TOTAL["total_tokens"] += u.total_tokens


def usage_total() -> dict:
    """进程启动以来累计的 MiniMax token 用量（payg 与 token_plan 合计）。"""
    return dict(_USAGE_TOTAL)


# ---------------------------------------------------------------- 调用 --------
async def list_models(conf: MiniMaxConf, client: httpx.AsyncClient) -> list[str]:
    """拉取 MiniMax 可用模型列表（/models，OpenAI 兼容）。"""
    headers = {"Authorization": f"Bearer {conf.api_key}"} if conf.api_key else {}
    try:
        r = await client.get(f"{conf.base_url}/models", headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        br = _extract_base_resp(data)
        if br.get("status_code"):
            raise map_error(r.status_code, r.text or "", data, conf.billing_mode)
        items = data.get("data") if isinstance(data, dict) else None
        return [m.get("id") for m in items if isinstance(m, dict) and m.get("id")] if isinstance(items, list) else []
    except httpx.HTTPError as exc:
        body = exc.response.text[:300] if getattr(exc, "response", None) is not None else str(exc)
        data = {}
        try:
            data = exc.response.json()
        except (ValueError, AttributeError):
            pass
        raise map_error(getattr(exc.response, "status_code", 0), body, data, conf.billing_mode)


async def chat(conf: MiniMaxConf, messages: list[dict], temperature: float = 0.8,
               max_tokens: int = 768, use_thinking: bool = True,
               client: httpx.AsyncClient | None = None,
               timeout: httpx.Timeout | None = None) -> tuple[str, LLMUsage]:
    """非流式文本生成，返回 (正文, 本次用量)。

    与 server.py llm_chat 相同的错误/重试语义：5xx/429/临时错误码指数退避重试 3 次；
    空正文重试并降级 thinking；余额不足/超限（1008/2056）直接报错不重试。
    """
    own_client = client is None  # 未传入时自建并负责关闭
    client = httpx.AsyncClient() if own_client else client
    url = f"{conf.base_url}/chat/completions"
    # M3 思考模式（adaptive）会先输出一段长推理，默认 768 预算会被思考吃光、
    # 正文为空（finish=length）；开启思考时放大预算，保证思考+正文都能输出。
    max_tokens = max(max_tokens, _THINKING_MIN_TOKENS) if use_thinking else max_tokens
    payload = build_payload(messages, conf.model, temperature, max_tokens, use_thinking, stream=False)
    timeout = timeout or _timeout(max_tokens)
    last_err: Exception | None = None
    try:
        for attempt in range(3):
            try:
                r = await client.post(url, json=payload,
                                      headers={"Authorization": f"Bearer {conf.api_key}",
                                               "Content-Type": "application/json"},
                                      timeout=timeout)
                data = r.json()
                br = _extract_base_resp(data)
                br_code = br.get("status_code")
                # 2013 参数错误可能是 MiniMax 不支持 thinking 字段：去掉后重试一次
                if br_code == 2013 and "thinking" in payload and attempt < 2:
                    payload.pop("thinking", None)
                    _log.info("minimax retry: attempt=%d reason=unsupported_thinking", attempt + 1)
                    await asyncio.sleep(_LLM_RETRY_BACKOFF[min(attempt, len(_LLM_RETRY_BACKOFF) - 1)])
                    continue
                if r.status_code >= 400:
                    raise map_error(r.status_code, r.text or "", data, conf.billing_mode)
                if br_code:
                    raise map_error(r.status_code, r.text or "", data, conf.billing_mode)
                choices = data.get("choices") or []
                if not choices:
                    raise HTTPException(502, f"MiniMax 响应缺少 choices（model={conf.model}），请检查云端模型配置")
                choice = choices[0]
                msg = choice.get("message") or {}
                content, think_text = strip_think((msg.get("content") or ""))
                finish_reason = choice.get("finish_reason") or ""
                # reasoning_split 生效时思考在 reasoning_content；未生效时内嵌 <think> 已剥离
                if not content:
                    reasoning = (msg.get("reasoning_content") or "").strip() or think_text
                    if reasoning:
                        content = reasoning
                if not content:
                    if attempt < 2:
                        last_err = HTTPException(502, "MiniMax 返回了空回复")
                        if use_thinking and "thinking" in payload:
                            payload["thinking"] = {"type": "disabled"}
                        _log.info("minimax retry: attempt=%d reason=empty_content", attempt + 1)
                        await asyncio.sleep(_LLM_RETRY_BACKOFF[min(attempt, len(_LLM_RETRY_BACKOFF) - 1)])
                        continue
                    detail = f"model={conf.model}"
                    if finish_reason:
                        detail += f", finish_reason={finish_reason}"
                    raise HTTPException(502, f"MiniMax 返回了空回复，请重试；若持续失败请检查模型配置（{detail}）")
                usage = parse_usage(data)
                _accumulate_usage(usage)
                return content, usage
            except HTTPException:
                raise
            except httpx.HTTPStatusError as exc:
                # raise_for_status 兜底（正常情况下 4xx 已走 map_error）
                body = exc.response.text[:300] if exc.response is not None else str(exc)
                data = {}
                try:
                    data = exc.response.json()
                except (ValueError, AttributeError):
                    pass
                raise map_error(exc.response.status_code, body, data, conf.billing_mode)
            except httpx.TransportError as exc:
                if attempt == 2:
                    raise HTTPException(502, f"MiniMax 连接失败: {exc}")
                last_err = exc
                _log.info("minimax retry: attempt=%d transport_err=%s", attempt + 1, exc)
                await asyncio.sleep(_LLM_RETRY_BACKOFF[min(attempt, len(_LLM_RETRY_BACKOFF) - 1)])
            except (KeyError, IndexError, ValueError) as exc:
                raise HTTPException(502, f"MiniMax 响应解析失败: {exc}")
        raise HTTPException(502, f"MiniMax 调用失败: {last_err}")
    finally:
        if own_client:
            await client.aclose()


async def chat_stream(conf: MiniMaxConf, messages: list[dict], temperature: float = 0.8,
                      max_tokens: int = 768, use_thinking: bool = True,
                      client: httpx.AsyncClient | None = None,
                      timeout: httpx.Timeout | None = None) -> AsyncIterator[str]:
    """流式文本生成：逐个 yield 正文 delta（不含 reasoning_content）。

    与 server.py llm_chat_stream 相同的语义：SSE 场景无法撤回已输出内容，
    重试仅限「尚未输出任何 delta」的阶段；流式 usage 从末尾 chunk 解析并累计。
    """
    own_client = client is None
    client = httpx.AsyncClient() if own_client else client
    url = f"{conf.base_url}/chat/completions"
    # 同 chat()：M3 思考模式需预留思考+正文预算，否则思考吃光 max_tokens 正文为空
    max_tokens = max(max_tokens, _THINKING_MIN_TOKENS) if use_thinking else max_tokens
    payload = build_payload(messages, conf.model, temperature, max_tokens, use_thinking, stream=True)
    timeout = timeout or _timeout(max_tokens)
    yielded = False
    last_err: Exception | None = None
    try:
        for attempt in range(3):
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            think_parts: list[str] = []
            in_think = False  # 流式 <think> 标签跨 chunk 状态（M2.x 未按 reasoning_split 拆分时）
            usage = LLMUsage()
            try:
                async with client.stream(
                    "POST", url, json=payload,
                    headers={"Authorization": f"Bearer {conf.api_key}", "Content-Type": "application/json"},
                    timeout=timeout,
                ) as r:
                    if r.status_code >= 400:
                        await r.aread()
                        body = (r.text or "")[:300]
                        data = {}
                        try:
                            data = r.json()
                        except (ValueError, AttributeError):
                            pass
                        # 2013 参数错误且未输出内容：去掉 thinking 后重试一次
                        br = _extract_base_resp(data)
                        if br.get("status_code") == 2013 and "thinking" in payload and not yielded and attempt < 2:
                            payload.pop("thinking", None)
                            _log.info("minimax_stream retry: attempt=%d reason=unsupported_thinking", attempt + 1)
                            continue
                        raise map_error(r.status_code, body, data, conf.billing_mode)
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            obj = json.loads(data)
                        except ValueError:
                            continue
                        br = _extract_base_resp(obj)
                        if br.get("status_code"):
                            raise map_error(0, json.dumps(obj, ensure_ascii=False)[:300], obj, conf.billing_mode)
                        choices = obj.get("choices") or []
                        if not choices:
                            # 流式末尾 chunk：include_usage=True 时携带 usage
                            u = parse_usage(obj)
                            if u.total_tokens:
                                usage = u
                            continue
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            # M2.x 未按 reasoning_split 拆分时思考内嵌 <think>：状态机剥离，
                            # 正文部分收集后统一 yield（避免生成器跨 yield 挂起导致状态错乱）
                            body_parts: list[str] = []

                            def _emit(t):
                                body_parts.append(t)

                            in_think = _stream_split_think(piece, in_think, think_parts, _emit)
                            for t in body_parts:
                                content_parts.append(t)
                                yielded = True
                                yield t
                        rp = delta.get("reasoning_content")
                        if rp:
                            reasoning_parts.append(rp)
                content = "".join(content_parts).strip()
                if not content and (reasoning_parts or think_parts):
                    # thinking 模型只输出了思考没出正文：兜底输出一次
                    yield "".join(reasoning_parts + think_parts).strip()
                if usage.total_tokens:
                    _accumulate_usage(usage)
                return
            except HTTPException:
                raise
            except httpx.TransportError as exc:
                if yielded or attempt == 2:
                    raise HTTPException(502, f"MiniMax 连接中断: {exc}")
                last_err = exc
                _log.info("minimax_stream retry: attempt=%d transport_err=%s", attempt + 1, exc)
                await asyncio.sleep(_LLM_RETRY_BACKOFF[min(attempt, len(_LLM_RETRY_BACKOFF) - 1)])
            except (KeyError, IndexError, ValueError) as exc:
                raise HTTPException(502, f"MiniMax 响应解析失败: {exc}")
        raise HTTPException(502, f"MiniMax 调用失败: {last_err}")
    finally:
        if own_client:
            await client.aclose()


async def query_quota(conf: MiniMaxConf, client: httpx.AsyncClient | None = None) -> dict:
    """Token Plan 配额校验：用订阅 Key 查套餐额度/积分余额（官方 /v1/token_plan/remains）。

    返回 {ok, mode, data}：ok=False 时 data 为错误说明（鉴权失败/不可用）。
    按量付费（payg）模式无此接口，返回提示走 usage 计费。
    """
    own_client = client is None
    client = httpx.AsyncClient() if own_client else client
    try:
        if conf.billing_mode != "token_plan":
            return {"ok": False, "mode": "payg",
                    "data": "当前为按量付费（payg）模式，无 Token Plan 配额；按 token 实时计费，用量见响应 usage"}
        if not conf.api_key:
            return {"ok": False, "mode": "token_plan", "data": "未配置订阅 Key：请在设置里填写 MiniMax Token Plan 订阅 Key"}
        url = conf.quota_endpoint()
        r = await client.get(url, headers={"Authorization": f"Bearer {conf.api_key}"}, timeout=15)
        data = {}
        try:
            data = r.json()
        except (ValueError, AttributeError):
            pass
        br = _extract_base_resp(data)
        if br.get("status_code"):
            raise map_error(r.status_code, r.text or "", data, conf.billing_mode)
        if r.status_code >= 400:
            raise map_error(r.status_code, r.text or "", data, conf.billing_mode)
        return {"ok": True, "mode": "token_plan", "data": data}
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        body = exc.response.text[:300] if getattr(exc, "response", None) is not None else str(exc)
        data = {}
        try:
            data = exc.response.json()
        except (ValueError, AttributeError):
            pass
        raise map_error(getattr(exc.response, "status_code", 0), body, data, conf.billing_mode)
    finally:
        if own_client:
            await client.aclose()
