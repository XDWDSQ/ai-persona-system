# -*- coding: utf-8 -*-
"""历史清洗与文本后处理的健壮性回归。

_clean_history 在 /api/chat 主链路上：history 既是客户端入参，又会经
sessions.json 多端同步反复回灌。畸形条目（非 dict、content 是数字/列表/字典）
以前会抛 AttributeError 让整轮对话 500，而不是跳过这一条。

运行：python test_text_hardening.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server_pkg.text_utils import _clean_history  # noqa: E402

_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


MALFORMED = [
    "hello",                      # 条目是字符串
    None,                         # 条目是 null
    42,
    {"role": "user"},             # 缺 content
    {"role": "assistant", "content": {"a": 1}},   # content 是字典
    {"role": "user", "content": ["x"]},           # content 是列表
    {"role": "user", "content": 123},             # content 是数字
]


def test_no_exception_on_malformed():
    try:
        out = _clean_history(MALFORMED, "当前消息")
        check("畸形 history 不再抛异常", isinstance(out, list), f"out={out}")
    except Exception as exc:  # noqa: BLE001
        check("畸形 history 不再抛异常", False, f"{type(exc).__name__}: {exc}")


def test_good_entries_survive():
    hist = MALFORMED + [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "我在"}]
    out = _clean_history(hist, "x")
    texts = [m["content"] for m in out if m["role"] in ("user", "assistant")]
    check("正常条目仍被保留", "你好" in texts and "我在" in texts, f"out={out}")
    check("所有输出条目结构合法",
          all(isinstance(m, dict) and isinstance(m.get("content"), str) for m in out), f"out={out}")


def test_semantics_unchanged():
    check("连续重复被折叠",
          _clean_history([{"role": "user", "content": "a"}, {"role": "user", "content": "a"}], "x")
          == [{"role": "user", "content": "a"}])
    check("等于当前消息的尾条目被去掉",
          _clean_history([{"role": "user", "content": "cur"}], "cur") == [])
    check("空 content 被丢弃",
          _clean_history([{"role": "user", "content": "   "}], "x") == [])
    check("非 user/assistant 角色被丢弃",
          _clean_history([{"role": "system", "content": "s"}], "x") == [])
    long_txt = "开" * 2000 + "尾" * 300
    clipped = _clean_history([{"role": "assistant", "content": long_txt}], "x", clip_long_replies=True)
    plain = _clean_history([{"role": "assistant", "content": long_txt}], "x", clip_long_replies=False)
    check("本地模型截断长历史回复", len(clipped[0]["content"]) < len(long_txt)
          and "省略" in clipped[0]["content"], f"len={len(clipped[0]['content'])}")
    check("云端模型保留长历史回复全文", len(plain[0]["content"]) == len(long_txt))
    check("只取最近 20 条", len(_clean_history([{"role": "user", "content": f"m{i}"}
                                          for i in range(50)], "x")) == 20)


def test_cloud_keep_last_full():
    """云端策略：最近一条 assistant 长回复保全文（紧接追问需要完整上文），
    更早的长回复首尾裁剪。旧行为是全部不截断，长文后每轮白带上万字旧文。"""
    old_long = "早" * 2000 + "尾" * 300
    new_long = "先" * 2000 + "终" * 300
    hist = [
        {"role": "user", "content": "写第一篇长文"},
        {"role": "assistant", "content": old_long},
        {"role": "user", "content": "再写一篇"},
        {"role": "assistant", "content": new_long},
        {"role": "user", "content": "上一篇写了什么"},
    ]
    out = _clean_history(hist, "上一篇写了什么", clip_long_replies=True, keep_last_full=True)
    ass = [m["content"] for m in out if m["role"] == "assistant"]
    check("云端：窗口内最近一条长回复保全文", ass[-1] == new_long, f"len={len(ass[-1])}")
    check("云端：更早的长回复被裁剪", "省略" in ass[0] and len(ass[0]) < len(old_long))
    # 本地策略（keep_last_full=False）：最近一条也必须裁，否则 -c 8192 装不下
    out_local = _clean_history(hist, "上一篇写了什么", clip_long_replies=True, keep_last_full=False)
    ass_local = [m["content"] for m in out_local if m["role"] == "assistant"]
    check("本地：所有长回复都裁剪（含最近一条）",
          all("省略" in c and len(c) < 2000 for c in ass_local),
          str([len(c) for c in ass_local]))
    # 只有一条长回复且它就是最近一条：云端保全文
    single = [{"role": "user", "content": "写长文"},
              {"role": "assistant", "content": old_long}]
    out_s = _clean_history(single, "追问", clip_long_replies=True, keep_last_full=True)
    check("云端：唯一且最近的长回复保全文",
          [m for m in out_s if m["role"] == "assistant"][0]["content"] == old_long)


if __name__ == "__main__":
    for fn in (test_no_exception_on_malformed, test_good_entries_survive,
               test_semantics_unchanged, test_cloud_keep_last_full):
        print(f"\n--- {fn.__name__} ---")
        fn()
    print("\n" + "=" * 50)
    print(f"历史清洗健壮性测试完成，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)
