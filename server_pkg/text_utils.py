# -*- coding: utf-8 -*-
"""纯文本/标记/校验工具（自 server.py 迁移；_fix_addressing 另做快路径优化）。

依赖仅标准库 + role_engine（独立模块，无循环导入）。
所有函数与迁移前逐字节等价（_fix_addressing 除外：新增的快路径门禁
经论证与原正则组一一对应，无触发子串时各 sub 必然是 no-op）。
"""
from __future__ import annotations

import hashlib
import re

import role_engine


# 密钥掩码：未配置访问口令时 /api/status 用「*** + 尾4位」脱敏返回，
# 保存接口识别 *** 前缀即忽略该字段（不把掩码持久化）。用户填真实 key 不带此前缀，正常保存。
_KEY_MASK_PREFIX = "***"


def _mask_key(key: str) -> str:
    """密钥脱敏：非空返回 *** + 尾 4 位；空串原样返回。"""
    key = (key or "").strip()
    if not key:
        return ""
    return _KEY_MASK_PREFIX + key[-4:]


def _is_masked_key(value: str) -> bool:
    """判断提交的密钥值是否为掩码（保存时应忽略）。"""
    return bool(value) and str(value).startswith(_KEY_MASK_PREFIX)


# 凭据类字段的叶子名。**按名字兜住，不按已知路径枚举** ——
# /api/status 曾逐个字段手写脱敏，结果漏了 local.api_key 与 voice.mimo.api_key：
# 用户在设置页新增任意 provider、或填了本地模型的 key，就会随每次状态查询明文
# 流过公网隧道。deploy/pack_cloud.py 的公网包脱敏与本常量同源。
# 匹配是全等（忽略大小写/空格），所以 news.keyword 这类不会被误伤。
SENSITIVE_LEAVES = {
    "api_key", "apikey", "access_key", "secret_key", "app_secret",
    "access_token", "refresh_token", "token", "token_plan_api_key",
    "password", "passwd", "secret", "webhook_secret",
}


def _is_sensitive_leaf(key) -> bool:
    return isinstance(key, str) and key.strip().lower() in SENSITIVE_LEAVES


def mask_credentials(obj):
    """就地递归脱敏一切凭据字段（***+尾4），非字符串/空值原样保留。

    用于所有把 config.json 内容回传给前端的响应体。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _is_sensitive_leaf(k):
                if isinstance(v, str) and v:
                    obj[k] = _mask_key(v)
            elif isinstance(v, (dict, list)):
                mask_credentials(v)
    elif isinstance(obj, list):
        for item in obj:
            mask_credentials(item)
    return obj


def _safe_float(v, default: float = 0.0) -> float:
    """安全转 float：字符串/None/非法值一律回退默认，绝不抛异常。

    用于读取可能被手改/损坏的 JSON 缓存字段（ts/lat/lng 等），
    这些字段在对话主链路被消费，一次 ValueError 会整条对话 500。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_int(v, default: int = 0) -> int:
    """安全转 int：非法值回退默认（与 _safe_float 同思路）。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


_SEARCH_RE = re.compile(r"(?:\[|【)(?:search|搜索)\s*[:：]\s*([^\]】\r\n]{1,160})(?:\]|】)")


def _extract_search_query(text: str) -> str:
    m = _SEARCH_RE.search(text or "")
    return m.group(1).strip()[:160] if m else ""


def _strip_search_markers(text: str) -> str:
    return _SEARCH_RE.sub("", text or "").strip()


# 风格标记解析：LLM 在回复开头用 [style:xxx] 标注朗读风格，让 TTS 按情绪动态合成。
# LLM 不总是把标记放最开头（实测有放在中间/末尾的情况），因此匹配全文任意位置的
# [style:x] / 【风格：x】 标记（兼容全角括号/冒号、大小写）：第一个匹配作为风格，
# 并把所有标记从正文剥离，避免标记残留进展示文本和 TTS 朗读。
_STYLE_RE = re.compile(r"[\[【](?:style|风格)\s*[:：]\s*([^\]】\r\n]+?)\s*[\]】]", re.I)


def parse_style_prefix(text: str, fallback: str = "") -> tuple[str, str]:
    """从 LLM 输出中拆出 (style, reply)。

    匹配任意位置的 [style:xxx] / 【风格：xxx】 标记：第一个匹配作为 style，
    所有匹配从正文剥离（标记独占一行时留下的多余空行一并折叠）。
    无标记时 style=fallback，reply=原文。
    """
    text = (text or "").strip()
    if not text:
        return fallback, ""
    style = fallback
    matched = False
    for m in _STYLE_RE.finditer(text):
        if not matched:
            style = m.group(1).strip() or fallback
            matched = True
    if not matched:
        return fallback, text
    reply = _STYLE_RE.sub("", text)
    reply = re.sub(r"[ \t]*\n[ \t]*", "\n", reply)
    reply = re.sub(r"\n{3,}", "\n\n", reply)
    return style, reply.strip()


def _clean_history(history: list[dict], current: str, clip_long_replies: bool = True,
                   keep_last_full: bool = False) -> list[dict]:
    """压缩发送给模型的历史：折叠连续重复、去掉空消息、规整旧回复里的 style 标记。

    旧版前端曾把同一条用户消息 push 后整体发送，sessions 里因此残留连续重复；
    这些重复会让小模型把同一句当成两条输入，更容易机械复读。

    clip_long_replies：是否截断超长的历史 assistant 回复。本地模型必须截断
    （-c 8192 装不下上万字全文）。
    keep_last_full：云端模型配套使用 —— 只截断**更早**的长回复，窗口内最近一条
    assistant 回复保留全文（紧接的追问最需要上一条完整内容）。旧版云端对全部 20
    条历史都不截断：一篇一万字长文写完后，之后 20 轮对话每轮都白带上万字旧文
    （约 1.6 万 token/轮），是云端最大的一笔固定浪费。本地小窗口传 True 也没有
    意义（最近一条若上万字照样装不下），因此本地仍全部截断。"""
    cleaned: list[dict] = []
    current = (current or "").strip()
    window = history[-20:]
    # 窗口内最后一条 assistant 的位置：keep_last_full 时只豁免它
    last_assistant_idx = -1
    if clip_long_replies and keep_last_full:
        for i in range(len(window) - 1, -1, -1):
            m = window[i]
            if isinstance(m, dict) and m.get("role") == "assistant":
                last_assistant_idx = i
                break
    for idx, m in enumerate(window):
        # /api/chat 的 history 是客户端入参，且会经 sessions.json 多端同步回来：
        # 条目不是 dict、或 content 是数字/列表/字典时，原来的 m.get(...).strip()
        # 直接 AttributeError -> 主对话 500。这种条目跳过即可，不该拖垮整轮对话。
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        raw = m.get("content")
        if raw is None:
            continue
        if not isinstance(raw, str):
            raw = raw if isinstance(raw, (int, float)) else ""
        content = str(raw).strip()
        if not content or role not in ("user", "assistant"):
            continue
        if role == "assistant":
            _, content = parse_style_prefix(content)
            # 超长历史回复只保留首尾，让模型知道「上文写过什么」即可；
            # 最近一条（紧接追问的那一条）在云端大窗口下保留全文
            if (clip_long_replies and len(content) > 1600
                    and not (keep_last_full and idx == last_assistant_idx)):
                content = content[:1000] + "\n……（中间内容省略）……\n" + content[-300:]
        if not content:
            continue
        if cleaned and cleaned[-1]["role"] == role and cleaned[-1]["content"] == content:
            continue
        cleaned.append({"role": role, "content": content})
    if cleaned and cleaned[-1]["role"] == "user" and cleaned[-1]["content"] == current:
        cleaned.pop()
    return cleaned


_META_LINE_START_RE = re.compile(r"^(?:注|备注|温馨提示|提示|说明|PS|p\.s\.)[:：]", re.I)
_META_LINE_KW_RE = re.compile(
    r"虚构演绎|虚构内容|纯属虚构|虚拟演绎|理性看待|请理性|免责声明|仅供参考|仅供娱乐|"
    r"作为AI|作为一个人工智能|作为智能助手|作为虚拟|AI助手|AI 助手|"
    r"科学作息|健康睡眠|性别认知|的行为准则|希望大家|祝愿大家|祝大家|祝每位|"
    r"这是我在扮演|扮演.*?提醒自己"
)


def _strip_meta_notes(text: str) -> str:
    """剥离模型偶发追加的元话语（/api/chat 与 /api/greeting 共用兜底）。"""
    if not text:
        return text
    kept: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            kept.append(ln)
            continue
        if _META_LINE_START_RE.match(s) or (len(s) <= 120 and _META_LINE_KW_RE.search(s)):
            continue
        kept.append(ln)
    out = "\n".join(kept)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


_TT_PUNCT_TRANS = str.maketrans(
    {",": "，", ".": "。", "?": "？", "!": "！", ":": "：", ";": "；", "(": "（", ")": "）"}
)


# 标点规整：原 7 条正则全部预编译，逐个 sub 调用，语义严格等价。
#   ponytail: 7 次 sub 调用的 Python 开销 <1μs/次，合并会引入交替正则的漏匹配 bug；
#   真正的性能收益来自「预编译 + 避免每次 re.sub 查模块级 _cache 字典」。
_TT_RE_WS_COLLAPSE = re.compile(r"[ \t]+")
_TT_RE_NEWLINES    = re.compile(r"\s*\n+\s*")
_TT_RE_PUNCT_DUP   = re.compile(r"([，。！？；：]){2,}")
_TT_RE_PUNCT_WS    = re.compile(r"\s*([，。！？；：、])\s*")
_TT_RE_PUNCT_LSTRIP = re.compile(r"^[，。！？；：、]+")
_TT_RE_PUNCT_RSTRIP = re.compile(r"[，。！？；：、]+$")


def normalize_tts_text(text: str) -> str:
    """规整朗读文本：统一中文标点、折叠换行、补齐句末标点，改善断句。"""
    text = (text or "").strip()
    if not text:
        return ""
    text = text.translate(_TT_PUNCT_TRANS)
    text = _TT_RE_WS_COLLAPSE.sub(" ", text)
    text = _TT_RE_NEWLINES.sub("。", text)
    text = _TT_RE_PUNCT_DUP.sub(r"\1", text)
    text = _TT_RE_PUNCT_WS.sub(r"\1", text)
    text = _TT_RE_PUNCT_LSTRIP.sub("", text)
    text = _TT_RE_PUNCT_RSTRIP.sub("", text)
    if text and not text.endswith(("。", "！", "？")):
        text += "。"
    return text


def _last_assistant_content(history: list[dict]) -> str:
    """返回历史中最后一条 assistant 消息（用于判断是否复读了上一条回复）。"""
    for m in reversed(history):
        if m.get("role") == "assistant":
            return m.get("content") or ""
    return ""


def _too_similar_to_last(text: str, history: list[dict]) -> bool:
    prev = _last_assistant_content(history)
    if not prev or not text:
        return False
    return role_engine._dup_sim(text, prev) >= 0.8


def _is_degenerate_reply(text: str) -> bool:
    """检测机械复读：空回复、连续 20+ 个相同字符、或某个 3-gram 占比超过 60%。"""
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return True
    if re.search(r"(.)\1{19,}", t):
        return True
    if len(t) >= 12:
        tris = [t[i:i + 3] for i in range(len(t) - 2)]
        counts: dict[str, int] = {}
        for g in tris:
            counts[g] = counts.get(g, 0) + 1
        if counts and max(counts.values()) / len(tris) > 0.6:
            return True
    return False


_TEST_SESSION_PREFIX = "t-"


def _is_test_session(item) -> bool:
    """判断会话或墓碑是否为测试数据（id 以 t- 开头）。"""
    sid = (item or {}).get("id") if isinstance(item, dict) else None
    return isinstance(sid, str) and sid.startswith(_TEST_SESSION_PREFIX)


def _sessions_fp(payload_str: str) -> str:
    """sessions.json 内容的轻量指纹（sha1 前 16 位）：多端同步用于快速判断
    服务端数据是否变化，避免每轮比对都让前端拉全量再本地合并。"""
    return hashlib.sha1(payload_str.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ 称呼纠偏 ----
# _fix_addressing 快路径门禁：每组正则至少需要一个字面触发子串才会命中，
# 无触发子串时各 sub 必然是 no-op，直接 strip 返回，省掉 ~60 次正则扫描。
# 等价性逐组核对：
#   自称组全含"老公"；叫错组全含"老婆"；"他"组 22 个字面量逐一列出；
#   女性自称组（女人/女孩/女生/妹子/人家/宝宝/棉袄/仙女）全覆盖，"的的人"清理
#   只可能由同组前置替换产生；thinking 标记/英文/风格标记/富文本标记各有字面锚点。
_FIX_ADDR_TA_PATTERNS = (
    "他的手", "他的脸", "他的肩", "他的背", "他的腰", "他的腿",
    "他靠过来", "他靠在", "他靠近", "靠近他", "他身边", "他笑了",
    "看着他", "对他说", "他在说", "他坐下", "他站起来", "他走过来",
    "他搂", "他抱", "他亲", "他摸",
)
_FIX_ADDR_TRIGGERS = (
    "老公", "老婆", "女人", "女孩", "女生", "妹子", "人家", "宝宝",
    "棉袄", "仙女", "好的，在处理", "好的，首先", "作为一个男", "首先，",
    "[", "(", "/", "state", "[=", "[/",
)
_FIX_ADDR_EN_RE = re.compile(r"[A-Za-z]{2,}")


def _fix_addressing(text: str) -> str:
    """后处理：自动纠正大帅回复中的称呼错误（4B 模型指令遵循弱，需兜底）。
    1) assistant 自称老公 -> 改为"我"或"老婆"
    2) 把用户叫老婆 -> 改为"老公"
    3) 第三人称"他"指代用户 -> 改为"你"
    4) 女性自称（女人/女孩子/妹子/人家/本宝宝）-> 删除或替换
    5) 英文夹杂（非赛事术语）-> 删除"""
    if not text:
        return text
    if (
        not any(t in text for t in _FIX_ADDR_TRIGGERS)
        and not any(p in text for p in _FIX_ADDR_TA_PATTERNS)
        and not _FIX_ADDR_EN_RE.search(text)
        and "  " not in text
    ):
        return text.strip()
    # 1) assistant 自称老公（"我是你老公"、"你老公我"、"老公我..."）
    text = re.sub(r"我是你老公，不是", "我不是", text)
    text = re.sub(r"我是你老公", "我是你老婆", text)
    text = re.sub(r"你老公我", "我", text)
    # "老公我错了" -> "老婆我错了"（自称语境）
    text = re.sub(r"(?<![他你])老公我(错了|这就|马上|现在)", r"老婆我\1", text)
    # 2) 称呼用户为老婆 -> 老公（行首/前导/句中各种模式）
    text = re.sub(r"^(\s*)老婆([,，!！~～:：\s])", r"\1老公\2", text)
    text = re.sub(r"(?<=[\n。！？～~])\s*老婆([,，!！~～:：\s])", r" 老公\1", text)
    text = re.sub(r"老婆大人", "老公大人", text)
    text = re.sub(r"老婆你(一大早|这是|也|看)", r"老公你\1", text)
    text = re.sub(r"晚安老婆", "晚安老公", text)
    text = re.sub(r"晚安，老婆", "晚安，老公", text)
    text = re.sub(r"睡吧老婆", "睡吧老公", text)
    text = re.sub(r"怎么了老婆", "怎么了老公", text)
    text = re.sub(r"看在你是老婆的份上", "看在你是老公的份上", text)
    text = re.sub(r"你是老婆", "你是老公", text)
    text = re.sub(r"(?<=[\n。！？～~\s])老婆，", "老公，", text)
    # 3) 第三人称"他"指代用户（在动作描写括号里最常见）
    for pat in _FIX_ADDR_TA_PATTERNS:
        text = re.sub(pat, pat.replace("他", "你"), text)
    # 4) 女性自称替换（覆盖复杂句式：我是/我可不是/我真是...的女人/女孩子/女生）
    text = text.replace("这是我的女人", "你是我的人")
    text = re.sub(r"我是(?:一个|个)?女人", "我是个男生", text)
    text = re.sub(r"我是(?:一个|个)?女孩子", "我是个男生", text)
    text = re.sub(r"我是(?:一个|个)?女生", "我是个男生", text)
    # 复杂句式兜底：我是...的女人 / 我可不是...的女人 / 我真是...的女人 -> ...的人
    text = re.sub(r"我(?:可|就|也|不|真是|不是|绝对不)[^，。！？\n]{0,18}的女人", lambda m: m.group(0)[:-2] + "的人", text)
    text = re.sub(r"我(?:可|就|也|真)?是(?:一个|个)?女孩子", "我是个男生", text)
    text = re.sub(r"我(?:可|就|也|真)?是(?:一个|个)?女生", "我是个男生", text)
    text = re.sub(r"我的女人", "我的人", text)
    text = re.sub(r"的女人", "的人", text)
    text = re.sub(r"的女孩子", "的人", text)
    text = re.sub(r"的女生", "的人", text)
    text = re.sub(r"的的人", "的人", text)
    text = re.sub(r"人家可", "我可", text)
    text = text.replace("本宝宝", "我")
    text = text.replace("小棉袄", "贴心人")
    text = re.sub(r"小仙女", "宝贝", text)
    # 6) Qwen3 thinking 泄漏：模型偶发先输出内部推理（"好的，在处理..."等）再输出回复。
    # 仅当全文足够长（>200 字，短回复如"首先，生日快乐！"是正常开头）且换行后还有内容时才截断；
    # 绝不用罐头文案整条替换，避免误伤正常回复
    for marker in ("好的，在处理", "好的，首先", "作为一个男", "首先，"):
        if text.startswith(marker) and len(text) > 200:
            nl = text.find("\n", 200)
            if nl < 0:
                nl = text.find("\n")
            if nl > 0:
                rest = text[nl:].strip()
                if rest:
                    text = rest
        if text.startswith(marker):
            break
    # 5) 英文夹杂（保留 KPL/AG/MVP/BO7/FMVP/KWC/TTG 等大写赛事术语；
    #    小写游戏术语 carry/bp/solo 等也保留，其余小写英文视为口癖清理）
    # 先保护 URL/邮箱：否则 https://... 里的字母会被整段抹掉
    _protected: list[str] = []

    def _stash(m):
        _protected.append(m.group(0))
        return f"\x00{len(_protected) - 1}\x00"

    text = re.sub(r"https?://[^\s，。！？、）】」』\]）]+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
                  _stash, text)
    _esports_keep = {"carry", "bp", "solo", "buff", "nerf", "gank", "poke"}
    text = re.sub(r"(?<![A-Z])(?![A-Z])[a-zA-Z]{2,}(?![A-Z])",
                   lambda m: m.group(0) if m.group(0).lower() in _esports_keep else "", text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: _protected[int(m.group(1))], text)
    # 6) 残留的风格/状态标记：[:xxx] (:xxx) [state:xxx] 等（8B 模型输出的变体格式）
    text = re.sub(r"\[[:：]\s*[^\]\r\n]{0,12}\]", "", text)
    text = re.sub(r"\([:：]\s*[^\)\r\n]{0,12}\)", "", text)
    text = re.sub(r"\[state:\s*[^\]\r\n]{0,12}\]", "", text)
    text = re.sub(r"\(state:\s*[^\)\r\n]{0,12}\)", "", text)
    # 7) 富文本/HTML 标签泄漏（8B 模型偶发输出 [=#FF5733][/] 或 [="微软雅黑"] 等）
    text = re.sub(r"\[=#?[0-9A-Fa-f]{0,8}\]", "", text)
    text = re.sub(r"\[=\s*\"[^\]]{0,20}\"\]", "", text)
    text = re.sub(r"\[/\s*\]", "", text)
    # 8) 「人家」变体清理（人家才/人家可/人家家）
    text = re.sub(r"人家才", "我才", text)
    text = re.sub(r"人家家", "我", text)
    text = re.sub(r"人家", "我", text)
    # 小女生/小女生语气
    text = re.sub(r"小女生", "小男生", text)
    # 清理遗留的空括号、多余空格
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"  +", " ", text)
    return text.strip()
