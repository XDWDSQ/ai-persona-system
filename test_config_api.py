# -*- coding: utf-8 -*-
"""配置 / 会话 / 健康检查 API 离线测试（TestClient，不起真实服务）。

运行：python test_config_api.py
覆盖：
  - POST /api/config 全字段写入落盘
  - 写入路径不把 .env 密钥持久化进 config.json
  - GET/PUT /api/sessions 往返一致性
  - _apply_env_overrides 密钥合并规则
  - GET /api/health 基础字段
隔离：CONFIG_PATH / SESSIONS_PATH 重定向到临时目录，不触碰真实 config.json / data/。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

_FAIL = 0

_ENV_KEY_NAMES = ("MIMO_API_KEY", "DEEPSEEK_API_KEY", "ARK_API_KEY",
                  "ALIYUN_API_KEY", "DASHSCOPE_API_KEY")


def check(name: str, cond: bool, detail: str = ""):
    global _FAIL
    if not cond:
        _FAIL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  <- {detail}" if detail and not cond else ""))


def _seed_config() -> dict:
    return {
        "provider": "local",
        "local": {"base_url": "http://localhost:11434/v1", "model": "Qwen3.5-4B-Q4_K_M"},
        "cloud": {"base_url": "", "model": "", "api_key": ""},
        "voice": {"provider": "local"},
        "roles": {},
    }


def setup_isolation() -> dict:
    """把 CONFIG_PATH / SESSIONS_PATH 重定向到临时目录，并清空 mtime 缓存。"""
    state = {
        "CONFIG_PATH": server.CONFIG_PATH,
        "SESSIONS_PATH": server.SESSIONS_PATH,
        "td": Path(tempfile.mkdtemp(prefix="cfg_api_test_")),
    }
    server.CONFIG_PATH = state["td"] / "config.json"
    server.SESSIONS_PATH = state["td"] / "sessions.json"
    server.CONFIG_PATH.write_text(
        json.dumps(_seed_config(), ensure_ascii=False, indent=2), encoding="utf-8")
    server._cfg_cache["_mtime_ns"] = 0
    server._cfg_cache["_value"] = {}
    server._sess_cache["_mtime_ns"] = 0
    server._sess_cache["_value"] = []
    return state


def teardown_isolation(state: dict) -> None:
    server.CONFIG_PATH = state["CONFIG_PATH"]
    server.SESSIONS_PATH = state["SESSIONS_PATH"]
    # 归零 mtime 缓存，下次访问自动重读真实文件
    server._cfg_cache["_mtime_ns"] = 0
    server._cfg_cache["_value"] = {}
    server._sess_cache["_mtime_ns"] = 0
    server._sess_cache["_value"] = []


def _read_disk_config() -> dict:
    return json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))


def _save_env() -> dict:
    return {k: os.environ.get(k) for k in _ENV_KEY_NAMES}


def _restore_env(saved: dict) -> None:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ---------------------------------------------------------------- config 写入 --------
def test_post_config_full_fields(client):
    payload = {
        "provider": "cloud",
        "local_base_url": "http://127.0.0.1:1234/v1",
        "local_model": "qwen3-test",
        "cloud_provider": "mimo",
        "cloud_base_url": "https://fake.example/v1",
        "cloud_api_key": "sk-" "test-123",
        "cloud_model": "mimo-test-model",
        "cloud_thinking": True,
        "persona": "测试人设内容",
        "voice_language": "English",
        "voice_provider": "aliyun",
        "voice_style": "温柔",
        "aliyun_api_key": "sk-" "ali-test",
        "aliyun_base_url": "https://ali.example/v1",
        "aliyun_model": "qwen3-tts-flash",
        "aliyun_voice": "Cherry",
    }
    r = client.post("/api/config", json=payload)
    data = r.json()
    check("POST /api/config 返回 ok", r.status_code == 200 and data == {"ok": True},
          f"status={r.status_code} data={data}")
    cfg = _read_disk_config()
    ok = (
        cfg.get("provider") == "cloud"
        and cfg["local"]["base_url"] == "http://127.0.0.1:1234/v1"
        and cfg["local"]["model"] == "qwen3-test"
        and cfg["cloud"]["provider"] == "mimo"
        and cfg["cloud"]["base_url"] == "https://fake.example/v1"
        and cfg["cloud"]["api_key"] == "sk-" "test-123"
        and cfg["cloud"]["model"] == "mimo-test-model"
        and cfg["cloud"]["thinking"] is True
        and cfg.get("persona") == "测试人设内容"
        and cfg["voice"]["language"] == "English"
        and cfg["voice"]["provider"] == "aliyun"
        and cfg["voice"].get("manual_provider") is True
        and cfg["voice"]["style"] == "温柔"
        and cfg["voice"]["aliyun"] == {
            "api_key": "sk-" "ali-test",
            "base_url": "https://ali.example/v1",
            "model": "qwen3-tts-flash",
            "voice": "Cherry",
        }
    )
    check("全字段正确落盘", ok, json.dumps(cfg, ensure_ascii=False))
    entry = cfg.get("cloud_providers", {}).get("mimo", {})
    check("当前 provider 条目同步保存 key/model/base_url/thinking",
          entry.get("api_key") == "sk-" "test-123" and entry.get("model") == "mimo-test-model"
          and entry.get("base_url") == "https://fake.example/v1" and entry.get("thinking") is True,
          str(entry))


def test_post_config_env_key_not_persisted(client):
    """临时设置 env 密钥后保存配置（不带 key 字段），落盘 config 不应出现 env 密钥。"""
    server.CONFIG_PATH.write_text(
        json.dumps(_seed_config(), ensure_ascii=False, indent=2), encoding="utf-8")
    server._cfg_cache["_mtime_ns"] = 0
    saved_env = _save_env()
    os.environ["MIMO_API_KEY"] = "env-secret-mimo"
    try:
        r = client.post("/api/config", json={
            "provider": "cloud",
            "cloud_provider": "mimo",
            "cloud_base_url": "https://fake.example/v1",
            "cloud_model": "mimo-test-model",
        })
        check("不带 key 的保存请求返回 ok", r.status_code == 200 and r.json() == {"ok": True},
              f"status={r.status_code} data={r.json()}")
        cfg = _read_disk_config()
        entry_key = cfg.get("cloud_providers", {}).get("mimo", {}).get("api_key", "")
        check("env 密钥未持久化：mimo 条目 api_key 为空", entry_key == "", f"entry_key={entry_key!r}")
        check("env 密钥未持久化：cloud.api_key 为空", cfg["cloud"].get("api_key", "") == "",
              f"cloud.api_key={cfg['cloud'].get('api_key')!r}")
    finally:
        _restore_env(saved_env)


# ---------------------------------------------------------------- sessions --------
def test_sessions_roundtrip(client):
    sessions = [
        {"id": "s1", "title": "测试会话", "messages": [{"role": "user", "content": "你好"}]},
        {"id": "s2", "messages": []},
    ]
    r = client.put("/api/sessions", json={"sessions": sessions})
    check("PUT /api/sessions 返回 ok", r.status_code == 200 and r.json() == {"ok": True},
          f"status={r.status_code} data={r.json()}")
    r2 = client.get("/api/sessions")
    got = r2.json().get("sessions")
    check("GET 取回与 PUT 相同结构", r2.status_code == 200 and got == sessions, str(got))
    disk = json.loads(server.SESSIONS_PATH.read_text(encoding="utf-8"))
    check("sessions 落盘内容与请求一致", disk.get("sessions") == sessions, str(disk))


# ---------------------------------------------------------------- env overrides --------
def test_apply_env_overrides():
    saved_env = _save_env()
    try:
        os.environ["MIMO_API_KEY"] = "env-" "mimo"
        os.environ["DEEPSEEK_API_KEY"] = "env-ds"
        # 1) provider 条目已有非空 key：env 不覆盖
        cfg1 = {"cloud": {"provider": "mimo", "api_key": ""},
                "cloud_providers": {"mimo": {"api_key": "disk-" "key"}}}
        out1 = server._apply_env_overrides(cfg1)
        check("条目已有非空 key 时 env 不覆盖",
              out1["cloud_providers"]["mimo"]["api_key"] == "disk-" "key",
              str(out1["cloud_providers"]))
        # 2) 条目为空串：env 补入
        cfg2 = {"cloud": {"provider": "mimo"},
                "cloud_providers": {"mimo": {"api_key": ""}}}
        out2 = server._apply_env_overrides(cfg2)
        check("条目为空串时 env 补入",
              out2["cloud_providers"]["mimo"]["api_key"] == "env-" "mimo",
              str(out2["cloud_providers"]))
        # 3) 当前 provider 的 cloud.api_key 取自己 provider 的 key
        check("当前 cloud.api_key 取当前 provider 自己的 key",
              out2["cloud"]["api_key"] == "env-" "mimo", str(out2["cloud"]))
        cfg3 = {"cloud": {"provider": "deepseek"},
                "cloud_providers": {"mimo": {"api_key": "env-" "mimo"},
                                    "deepseek": {"api_key": "ds-" "disk"}}}
        out3 = server._apply_env_overrides(cfg3)
        check("切到 deepseek 不拿 mimo 的 key",
              out3["cloud"]["api_key"] == "ds-" "disk", str(out3["cloud"]))
        # 4) 只改副本，不动原 cfg
        check("env 覆盖只作用于副本", "api_key" not in cfg2["cloud"], str(cfg2))
    finally:
        _restore_env(saved_env)


# ---------------------------------------------------------------- health --------
def test_health(client):
    r = client.get("/api/health")
    data = r.json()
    keys_ok = all(k in data for k in ("ok", "uptime", "bg_tasks", "tts_cache_files"))
    check("GET /api/health 返回 200 且含 ok/uptime/bg_tasks/tts_cache_files",
          r.status_code == 200 and data.get("ok") is True and keys_ok,
          f"status={r.status_code} data={data}")


def main():
    state = setup_isolation()
    try:
        with TestClient(server.app) as client:
            test_post_config_full_fields(client)
            test_post_config_env_key_not_persisted(client)
            test_sessions_roundtrip(client)
            test_health(client)
        test_apply_env_overrides()
    finally:
        teardown_isolation(state)
    print(f"\n{'=' * 50}\n配置 API 测试完成，失败 {_FAIL} 组")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
