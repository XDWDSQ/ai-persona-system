# -*- coding: utf-8 -*-
"""pytest 公共 fixture：提供 TestClient 实例（与 test_config_api.py / test_search.py
直接运行方式相同的最小隔离：路径重定向到临时目录 + 关闭访问门禁），
保证 `pytest test_config_api.py test_search.py` 也能收集执行。"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 必须在 import server 之前：TestClient 起真实 lifespan，离线模式切断天气/角色
# 动态的启动预取与巡检，防止跑测试时真实调用外部搜索/云端 LLM（烧 token）。
# 直跑模式（python test_xxx.py，run_tests.bat）由同名环境变量覆盖。
os.environ.setdefault("AI_DISABLE_EXTERNAL", "1")

import pytest
from fastapi.testclient import TestClient

import server  # noqa: E402

_SEED_CONFIG = {
    "provider": "local",
    "local": {"base_url": "http://localhost:11434/v1", "model": "Qwen3.5-4B-Q4_K_M"},
    "cloud": {"base_url": "", "model": "", "api_key": ""},
    "voice": {"provider": "local"},
    "roles": {},
}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """TestClient 实例：配置/会话路径重定向到临时目录，不触碰真实 config.json / data/。"""
    state = {
        "CONFIG_PATH": server.CONFIG_PATH,
        "SESSIONS_PATH": server.SESSIONS_PATH,
        "ACCESS_TOKEN": server._access_token,
        "td": tmp_path_factory.mktemp("cfg_api_test"),
    }
    server.CONFIG_PATH = state["td"] / "config.json"
    server.SESSIONS_PATH = state["td"] / "sessions.json"
    server.CONFIG_PATH.write_text(
        json.dumps(_SEED_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    # 访问门禁是后加的，离线测试不测鉴权，临时关掉以免全部 401
    server._access_token = lambda: None
    server._cfg_cache["_mtime_ns"] = 0
    server._cfg_cache["_value"] = {}
    server._sess_cache["_mtime_ns"] = 0
    server._sess_cache["_value"] = []
    try:
        with TestClient(server.app) as client:
            yield client
    finally:
        server.CONFIG_PATH = state["CONFIG_PATH"]
        server.SESSIONS_PATH = state["SESSIONS_PATH"]
        server._access_token = state["ACCESS_TOKEN"]
        server._cfg_cache["_mtime_ns"] = 0
        server._cfg_cache["_value"] = {}
        server._sess_cache["_mtime_ns"] = 0
        server._sess_cache["_value"] = []
