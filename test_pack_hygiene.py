# -*- coding: utf-8 -*-
"""公网打包脱敏与出厂自检回归。

历史事故：可公网下载的 cloud_deploy_public.zip 里带着 data/（真实聊天记录、
用户照片、真人语音）与 access_token，而构建日志只打印「密钥已置空」。
这里锁住两条不变量：凭据按叶子名一律清空；包里有个人数据就让构建失败。

运行：python test_pack_hygiene.py
"""
import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "deploy"))

from pack_cloud import (  # noqa: E402
    SENSITIVE_LEAVES,
    _is_blank_secret,
    blank_sensitive,
    verify_public_package,
)

_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


def _make_zip(members: dict) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="pack_hygiene_")) / "pkg.zip"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, obj in members.items():
            zf.writestr(name, json.dumps(obj, ensure_ascii=False) if obj is not None else "x")
    return tmp


def test_leaf_name_redaction():
    cfg = {
        "access_token": "REAL-TOKEN-32-chars",
        "local": {"api_key": "sk-local"},
        "cloud_providers": {
            "mimo": {"api_key": "sk-mimo"},
            "openrouter": {"api_key": "sk-new-provider"},  # 枚举白名单里没有的新 provider
        },
        "roles": {"dashuai": {"news": {"keyword": "成都AG 大帅"}}},  # 含 "key" 但不是凭据
        "voice": {"mimo": {"api_key": "sk-voice"}},
    }
    blank_sensitive(cfg)
    check("访问口令被清空", cfg["access_token"] == "", cfg["access_token"])
    check("已知 provider 密钥清空", cfg["cloud_providers"]["mimo"]["api_key"] == "")
    check("未登记的 provider 密钥也清空",
          cfg["cloud_providers"]["openrouter"]["api_key"] == "",
          cfg["cloud_providers"]["openrouter"]["api_key"])
    check("local.api_key 清空", cfg["local"]["api_key"] == "")
    check("语音子配置密钥清空", cfg["voice"]["mimo"]["api_key"] == "")
    check("keyword 不是凭据，不得被误伤",
          cfg["roles"]["dashuai"]["news"]["keyword"] == "成都AG 大帅")
    check("叶子名集合包含 api_key/access_token",
          {"api_key", "access_token"} <= SENSITIVE_LEAVES)


def test_verify_rejects_personal_data():
    z = _make_zip({
        "config.json": {"access_token": ""},
        "data/sessions.json": {"sessions": []},
        "data/uploads/att_deadbeefdead.jpg": None,
    })
    try:
        verify_public_package(z)
        check("含 data/ 成员必须构建失败", False, "verify 未抛错")
    except SystemExit as exc:
        check("含 data/ 成员必须构建失败", "data/" in str(exc.code), str(exc.code)[:120])


def test_verify_rejects_live_credentials():
    z = _make_zip({
        "config.json": {"access_token": "leaked", "cloud_providers": {"x": {"api_key": "sk-live"}}},
    })
    try:
        verify_public_package(z)
        check("凭据非空必须构建失败", False, "verify 未抛错")
    except SystemExit as exc:
        msg = str(exc.code)
        check("凭据非空必须构建失败", "凭据字段" in msg, msg[:120])
        check("失败信息只报路径不报值", "leaked" not in msg and "sk-live" not in msg, msg[:120])


def test_verify_rejects_env_and_internal_docs():
    z = _make_zip({
        ".env": None,
        "deploy/README_云电脑部署.md": None,
        "config.json": {"access_token": ""},
    })
    try:
        verify_public_package(z)
        check(".env / 内部部署文档必须拒绝", False, "verify 未抛错")
    except SystemExit as exc:
        msg = str(exc.code)
        check(".env 被拒绝", ".env" in msg, msg[:160])
        check("内部部署文档被拒绝", "部署文档" in msg, msg[:160])


def test_verify_passes_clean_package():
    z = _make_zip({
        "config.json": {"access_token": "", "cloud_providers": {"mimo": {"api_key": ""}}},
        "config.example.json": {"local": {"api_key": "none"}},
        "server.py": None,
    })
    try:
        verify_public_package(z)
        check("干净包通过自检", True)
    except SystemExit as exc:
        check("干净包通过自检", False, str(exc.code)[:200])


def test_placeholder_values():
    check("'none' 视为占位", _is_blank_secret("none"))
    check("掩码值视为占位", _is_blank_secret("***abcd"))
    check("空串视为占位", _is_blank_secret("   "))
    check("真实密钥不视为占位", not _is_blank_secret("sk-abcdef123456"))


if __name__ == "__main__":
    for fn in (test_leaf_name_redaction,
               test_verify_rejects_personal_data,
               test_verify_rejects_live_credentials,
               test_verify_rejects_env_and_internal_docs,
               test_verify_passes_clean_package,
               test_placeholder_values):
        print(f"\n--- {fn.__name__} ---")
        fn()
    print("\n" + "=" * 50)
    print(f"公网包脱敏自检测试完成，失败 {_FAIL} 项")
    sys.exit(1 if _FAIL else 0)
