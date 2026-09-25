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

os.environ.setdefault("AI_DISABLE_EXTERNAL", "1")  # 直跑本文件也切断后台外部请求（烧 token）
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
        {"id": "s1", "title": "测试会话", "history": [{"role": "user", "content": "你好"}]},
    ]
    r = client.put("/api/sessions", json={"sessions": sessions})
    put_data = r.json()
    check("PUT /api/sessions 返回 ok 且带文件指纹",
          r.status_code == 200 and put_data.get("ok") is True and bool(put_data.get("fp")),
          f"status={r.status_code} data={put_data}")
    r2 = client.get("/api/sessions")
    got = r2.json().get("sessions")
    check("GET 取回与 PUT 相同结构", r2.status_code == 200 and got == sessions, str(got))
    check("GET 响应带文件指纹且与 PUT 一致", r2.json().get("fp") == put_data.get("fp"), str(r2.json().get("fp")))
    r3 = client.get("/api/sessions/fingerprint")
    check("轻量指纹探测可用", r3.status_code == 200 and r3.json().get("fp") == put_data.get("fp"),
          f"status={r3.status_code} data={r3.json()}")
    disk = json.loads(server.SESSIONS_PATH.read_text(encoding="utf-8"))
    check("sessions 落盘内容与请求一致", disk.get("sessions") == sessions, str(disk))


def test_placeholder_sessions_filtered(client):
    """空占位会话（无 history、默认标题、未置顶未改名）合并时直接丢弃：
    前端空 localStorage 打开即产生一个，入库后会随并集同步淹没所有设备的会话列表。"""
    r = client.put("/api/sessions", json={"sessions": [
        {"id": "ph1", "title": "新对话", "history": []},
        {"id": "real1", "title": "真实会话", "history": [{"role": "user", "content": "在吗"}]},
        {"id": "ph2", "title": "新对话", "history": [], "manualTitle": False},
    ]})
    check("占位会话 PUT 返回 ok", r.status_code == 200, f"status={r.status_code}")
    got = {s["id"] for s in client.get("/api/sessions").json().get("sessions", [])}
    check("空占位会话被过滤", "ph1" not in got and "ph2" not in got and "real1" in got, str(got))
    # 非默认标题 / 置顶的空会话不算占位（可能有价值），应保留
    r2 = client.put("/api/sessions", json={"sessions": [
        {"id": "named", "title": "我起的名", "history": []},
        {"id": "pinned", "title": "新对话", "history": [], "pinned": True},
    ]})
    check("第二次 PUT 返回 ok", r2.status_code == 200, f"status={r2.status_code}")
    got2 = {s["id"] for s in client.get("/api/sessions").json().get("sessions", [])}
    check("改名/置顶的空会话保留", "named" in got2 and "pinned" in got2 and "real1" in got2, str(got2))


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
def test_persona_single_source(client):
    """人设单一真值：角色存在时只写 roles[active].persona，顶层不再复制（避免两份大文本重复）；
    无角色时（遗留配置）仍写顶层兜底。"""
    cfg = json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["active_role"] = "dashuai"
    cfg["roles"] = {"dashuai": {"name": "大帅", "persona": "旧人设"}}
    cfg["persona"] = "旧顶层人设"  # 存量重复：模拟旧版双写留下的顶层残留
    server.CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    server._cfg_cache["_mtime_ns"] = 0
    r = client.post("/api/config", json={"persona": "统一后的新人设"})
    check("persona 保存返回 ok", r.status_code == 200 and r.json() == {"ok": True},
          f"status={r.status_code} data={r.json()}")
    disk = json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))
    check("角色 persona 已更新",
          disk.get("roles", {}).get("dashuai", {}).get("persona") == "统一后的新人设",
          str(disk.get("roles")))
    check("顶层 persona 不再被复制（保留旧值，等待手工清理）",
          disk.get("persona") == "旧顶层人设", str(disk.get("persona")))
    check("current_persona 返回角色人设（角色优先）",
          server.current_persona(disk) == "统一后的新人设", server.current_persona(disk))
    # 无角色（roles 空）时：顶层作为兜底被写入
    cfg2 = json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))
    cfg2["roles"] = {}
    cfg2["active_role"] = ""
    server.CONFIG_PATH.write_text(json.dumps(cfg2, ensure_ascii=False), encoding="utf-8")
    server._cfg_cache["_mtime_ns"] = 0
    r2 = client.post("/api/config", json={"persona": "兜底人设"})
    check("无角色时顶层兜底写入", r2.status_code == 200
          and json.loads(server.CONFIG_PATH.read_text(encoding="utf-8")).get("persona") == "兜底人设",
          f"status={r2.status_code}")


def test_roles_apply_single_source(client):
    """切角色（POST /api/roles/apply）同样遵循人设单一真值：不把角色 persona 复制到顶层。
    （第四轮曾漏改此路径，导致每次切角色把顶层 persona 重新写回，破坏去重成果。）"""
    cfg = json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["active_role"] = "dashuai"
    cfg["roles"] = {
        "dashuai": {"name": "大帅", "persona": "大帅人设", "news": {"auto_refresh": False}},
        "xiaoni": {"name": "小拟", "persona": "小拟人设", "news": {"auto_refresh": False}},
    }
    cfg["persona"] = "旧顶层人设"  # 存量重复：模拟旧版双写残留
    server.CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    server._cfg_cache["_mtime_ns"] = 0
    r = client.post("/api/roles/apply", json={"key": "xiaoni"})
    check("切角色返回 ok", r.status_code == 200 and r.json().get("ok") is True,
          f"status={r.status_code} data={r.json()}")
    disk = json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))
    check("切角色后顶层 persona 不再被复制",
          disk.get("persona") == "旧顶层人设", str(disk.get("persona")))
    check("切角色返回值 persona 来自角色",
          r.json().get("persona") == "小拟人设", str(r.json()))
    check("current_persona 返回新角色人设",
          server.current_persona(disk) == "小拟人设", server.current_persona(disk))


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
            test_placeholder_sessions_filtered(client)
            test_persona_single_source(client)
            test_roles_apply_single_source(client)
            test_health(client)
        test_apply_env_overrides()
    finally:
        teardown_isolation(state)
    print(f"\n{'=' * 50}\n配置 API 测试完成，失败 {_FAIL} 组")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
