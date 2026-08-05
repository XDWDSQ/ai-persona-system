# -*- coding: utf-8 -*-
"""角色现实动态 v2 离线单元测试（不依赖网络/LLM）。

运行：ai-persona-system 的 venv python test_role_news_v2.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# 让测试可独立导入 server.py 内的纯函数（不启动 FastAPI）
sys.path.insert(0, str(Path(__file__).resolve().parent))

import importlib.util

# 直接编译 server.py 的语法（导入太重，仅验证函数级逻辑用子模块方式）
spec = importlib.util.spec_from_file_location("server_mod", "server.py")
# server.py 顶层会创建 FastAPI app 等，导入代价高；改用纯逻辑复制验证：
# 这里只验证我们新增函数的正确性，通过 exec 提取关键函数。

SRC = Path("server.py").read_text(encoding="utf-8")

# 提取纯逻辑函数源码并执行（不依赖 app/httpx 等）
_FUNCS = [
    "_log_role_news_missing",
    "_collect_role_facts",
    "_rule_news_summary",
    "_emotion_text",
    "_build_rule_cards",
    "_cards_to_summary",
    "_merge_cards",
    "_build_timeline",
]

import re

def _extract_func(name: str, src: str) -> str:
    """按缩进提取顶层函数体。"""
    m = re.search(rf"^(?P<indent>    )def {name}\(.*?^(?P=indent)(?=\S|$)", src, re.M | re.S)
    # 简单实现：找到 def 行后，收集后续缩进>=4 的行
    lines = src.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(f"def {name}("):
            start = i
            break
    if start is None:
        return ""
    out = [lines[start]]
    for ln in lines[start + 1:]:
        if ln and not ln.startswith(("    ", "        ", "\t")) and not ln.strip() == "":
            # 空行或缩进行继续；缩进为 0 的非空行结束
            if ln.startswith(("def ", "async def ", "class ", "@", "# ---")):
                break
            if not ln[0].isspace() and ln.strip():
                break
        out.append(ln)
        if ln.strip() and not ln.startswith("    "):
            # 缩进结束
            pass
    return "\n".join(out)

class _Safe:
    """安全沙箱：提供最小依赖桩，执行提取出的函数定义。"""
    pass


def _load_functions():
    """更稳妥的方式：把需要的辅助函数拼成一个模块再执行。"""
    ns: dict = {}
    # 需要的辅助依赖
    ns.update({
        "DATA_DIR": Path(tempfile.mkdtemp()),
        "time": time,
        "datetime": __import__("datetime").datetime,
        "json": json,
        "Path": Path,
        "_ROLE_NEWS_LOG_FILE": Path(tempfile.mkdtemp()) / "role_news_missing.log",
        "_ROLE_NEWS_CATEGORIES": ("当前处境", "近期活动", "关键事件", "人物关系"),
        "_safe_float": lambda v, d=0.0: (float(v) if v is not None else d) if str(v).replace('.','',1).isdigit() or isinstance(v,(int,float)) else d,
        "_memo_file_json": lambda path, default: dict(default),
        "_get_role_stores": lambda role: (_FakeMem(), _FakeState()),
        "_location_city": lambda: "广东省深圳市南山区",
        "_WEATHER_FILE": Path("data/weather.json"),
        "_log": __import__("logging").getLogger("test"),
        "_role_news_config": lambda: ("成都AG超玩会 大帅 孟家俊", "大帅"),
    })

    class _FakeMem:
        def load(self):
            return [{"text": "用户喜欢被哄睡", "created_at": "2026-08-04T04:34:27+08:00"}]
    class _FakeState:
        def get_decayed(self):
            return {"emotion": {"valence": 0.5, "arousal": 0.3}, "energy": 0.7, "intimacy": 1.0}
    ns["_FakeMem"] = _FakeMem
    ns["_FakeState"] = _FakeState

    src = SRC
    # 截取角色现实动态区块（从 _ROLE_NEWS_FILE = 到 _role_news_hint 前）
    start = src.index("_ROLE_NEWS_FILE = DATA_DIR")
    end = src.index("def _role_news_hint(")
    block = src[start:end]
    # 去掉 async 函数（依赖 llm_chat/web_search 的 _summarize 不需要测试）
    block = block.replace("async def _bg_role_news_refresh", "def _bg_role_news_refresh_placeholder")
    block = block.replace("async def _role_news_scheduler", "def _role_news_scheduler_placeholder")
    exec(compile(block, "<role_news_block>", "exec"), ns)
    return ns


def _load_real_functions():
    """真正导入 server 模块中的纯函数（server.py 顶层副作用已存在，直接 import 可行）。"""
    import server
    return server


PASS = 0
FAIL = 0

def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def main():
    print("== 角色现实动态 v2 离线测试 ==")
    try:
        mod = _load_real_functions()
        print("import server OK")
    except Exception as exc:
        print(f"import server 失败（可能缺依赖）：{exc}")
        mod = _load_functions()

    now = time.time()
    facts = {
        "persona": ["身份：成都AG超玩会.大帅", "简介：KPL 游走位"],
        "memory": [{"text": "用户喜欢被哄睡", "date": "2026-08-04"}],
        "state": {"emotion": {"valence": 0.5, "arousal": 0.3}, "energy": 0.7, "intimacy": 1.0},
        "location": "广东省深圳市南山区",
        "weather": "晴，25°C",
    }

    print("\n--- 1) 多来源聚合 _collect_role_facts ---")
    try:
        if hasattr(mod, "_collect_role_facts"):
            f = mod._collect_role_facts({"full_name": "成都AG超玩会.大帅", "desc": "KPL 游走位"}, {}, "dashuai")
            check("persona 提取", len(f.get("persona") or []) >= 1, str(f)[:200])
            check("memory 提取", len(f.get("memory") or []) >= 1, str(f.get("memory")))
            check("state 提取", f.get("state") is not None and isinstance(f.get("state"), dict))
        else:
            print("  - 跳过（无该函数）")
    except Exception as exc:
        check("_collect_role_facts", False, repr(exc))

    print("\n--- 2) 规则兜底卡片 _build_rule_cards（搜索无结果场景） ---")
    if hasattr(mod, "_build_rule_cards"):
        cards = mod._build_rule_cards("成都AG超玩会 大帅 孟家俊", [], facts, "大帅")
        check("产出 4 个类别", len(cards) == 4, f"got {len(cards)}")
        cats = {c["category"] for c in cards}
        check("类别齐全", cats == {"当前处境", "近期活动", "关键事件", "人物关系"}, str(cats))
        check("三态齐全", {c["status"] for c in cards} <= {"confirmed", "inferred", "missing"})
        sts = {c["status"] for c in cards}
        check("搜索空→近期活动=missing", any(c["category"] == "近期活动" and c["status"] == "missing" for c in cards))
        check("位置有→当前处境=confirmed", any(c["category"] == "当前处境" and c["status"] == "confirmed" for c in cards))
        check("记忆有→人物关系=confirmed", any(c["category"] == "人物关系" and c["status"] == "confirmed" for c in cards))
    else:
        print("  - 跳过")

    print("\n--- 3) 卡片→摘要 _cards_to_summary（不注入 missing） ---")
    if hasattr(mod, "_cards_to_summary"):
        cards = mod._build_rule_cards("kw", [], facts, "大帅")
        s = mod._cards_to_summary(cards)
        check("摘要非空", bool(s), s)
        check("摘要不含「暂无资料」", "暂无资料" not in s)
        check("摘要长度≤300", len(s) <= 300, str(len(s)))

    print("\n--- 4) 合并 LLM 卡片 _merge_cards（LLM 缺类→规则补齐） ---")
    if hasattr(mod, "_merge_cards"):
        llm_cards = [{"category": "近期活动", "status": "confirmed", "content": "x", "id": "a"}]
        rule_cards = mod._build_rule_cards("kw", [], facts, "大帅")
        merged = mod._merge_cards(llm_cards, rule_cards)
        cats = {c["category"] for c in merged}
        check("4 类齐全", cats == {"当前处境", "近期活动", "关键事件", "人物关系"}, str(cats))

    print("\n--- 5) 时间线 _build_timeline（日期倒序、去重） ---")
    if hasattr(mod, "_build_timeline"):
        cards = [{"category": "关键事件", "status": "confirmed", "content": "A", "date": "2026-08-05", "id": "1"},
                 {"category": "近期活动", "status": "confirmed", "content": "B", "date": "", "id": "2"},
                 {"category": "关键事件", "status": "confirmed", "content": "A", "date": "2026-08-05", "id": "3"}]
        tl = mod._build_timeline(cards, facts)
        check("时间线非空", len(tl) >= 3)
        check("去重生效", len(tl) == len({(i.get("date"), i.get("content")) for i in tl}))
        # 带日期的应排在无日期前
        dates = [i.get("date") for i in tl]
        idx_empty = [i for i, d in enumerate(dates) if not d]
        check("无日期条目排最后", (not idx_empty) or idx_empty == [len(dates) - 1], str(dates))

    print("\n--- 6) 情绪转文本 _emotion_text ---")
    if hasattr(mod, "_emotion_text"):
        t = mod._emotion_text({"emotion": {"valence": 0.2, "arousal": 0.3}, "energy": 0.3})
        check("低落场景", "低落" in t and "疲惫" in t, t)
        t2 = mod._emotion_text(None)
        check("None 安全", t2 == "")

    print("\n--- 7) 缺失日志 _log_role_news_missing（追加 JSONL） ---")
    if hasattr(mod, "_log_role_news_missing"):
        logfile = Path(tempfile.mkdtemp()) / "role_news_missing.log"
        mod._ROLE_NEWS_LOG_FILE = logfile
        mod._log_role_news_missing("联网搜索无结果", "近期活动", "kw")
        mod._log_role_news_missing("长期记忆为空", "人物关系", "kw")
        lines = logfile.read_text(encoding="utf-8").strip().splitlines()
        check("写入 2 条", len(lines) == 2, str(len(lines)))
        first = json.loads(lines[0])
        check("字段齐全", all(k in first for k in ("ts", "time", "role", "keyword", "category", "reason")), str(first))

    print("\n--- 8) v1→v2 缓存升级 _load_role_news ---")
    if hasattr(mod, "_load_role_news"):
        import types
        if isinstance(mod, types.ModuleType):
            tmpdir = Path(tempfile.mkdtemp())
            v1 = {"version": 1, "role": "大帅", "keyword": "kw", "text": "旧版摘要内容", "ts": now - 100}
            v1file = tmpdir / "role_news.json"
            v1file.write_text(json.dumps(v1, ensure_ascii=False), encoding="utf-8")
            orig_file, orig_memo = mod._ROLE_NEWS_FILE, mod._small_file_cache
            mod._ROLE_NEWS_FILE = v1file
            mod._small_file_cache = {}
            try:
                d = mod._load_role_news()
                check("version=2", d.get("version") == 2, str(d.get("version")))
                check("summary 迁移", d.get("summary") == "旧版摘要内容", d.get("summary"))
                check("cards 含 legacy 卡片", any(c.get("id") == "legacy" for c in d.get("cards", [])), str(d.get("cards")))
            finally:
                mod._ROLE_NEWS_FILE = orig_file
                mod._small_file_cache = orig_memo
        else:
            print("  - 跳过（非模块）")

    print(f"\n== 结果：{PASS} 通过 / {FAIL} 失败 ==")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
