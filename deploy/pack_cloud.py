# -*- coding: utf-8 -*-
"""打包 AI 拟人系统到云电脑的部署包（cloud_deploy.zip）。

用法：
    python deploy/pack_cloud.py
输出：
    cloud_deploy.zip —— 拷贝到云电脑解压即可运行

包含：后端代码 + 前端 + 配置密钥 + 会话/记忆/状态/音色/附件
排除：llm/（本地模型，云上用不到）、data/tts_cache（缓存）、
      data/outputs（试听文件）、.git、__pycache__、.workbuddy
"""
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "cloud_deploy.zip"

# 顶层文件（显式白名单）
TOP_FILES = [
    "server.py",
    "role_engine.py",
    "minimax_llm.py",      # MiniMax 云端文字适配器（server.py 顶层 import）
    "story_kpl2027.py",    # 2027 剧情引擎（server.py 顶层 import）
    "requirements.txt",
    "config.json",
    "config.example.json",
    ".env",
    ".env.example",
    ".gitignore",
    "README.md",
    "minimax_clone.py",
    "verify_chain.py",
]

# 顶层目录（白名单）
TOP_DIRS = [
    "xiaoni-ai-persona",   # 前端
    "server_pkg",          # server.py 下沉的无状态模块（顶层 import，缺了启动即 ModuleNotFoundError）
    "docs-specs",          # 设计文档
    "deploy",              # 云电脑部署说明与安装脚本
]

# data/ 下需要带走的（白名单）
DATA_KEEP = [
    "sessions.json",
    "weather.json",
    "role_news.json",  # 角色现实动态缓存
    "memory",      # 角色长期记忆
    "state",       # 角色状态
    "story",       # 2027 剧情状态
    "clone_ref",   # 音色参考音频
    "uploads",     # 历史附件（会话里引用）
]

# data/ 下排除的
DATA_SKIP = {"tts_cache", "outputs"}

def rel_parts(p: Path) -> list:
    return p.relative_to(ROOT).parts

def should_skip(p: Path) -> bool:
    parts = rel_parts(p)
    if any(part in ("__pycache__", ".git", ".workbuddy", "node_modules") for part in parts):
        return True
    if parts and parts[0] == "llm":
        return True
    if parts and parts[0] == "data":
        if len(parts) == 1:
            return False
        sub = parts[1]
        if sub in DATA_SKIP:
            return True
        if sub not in DATA_KEEP:
            return True
    return False

def main() -> int:
    if OUT.exists():
        OUT.unlink()
    count = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        def add(path: Path):
            nonlocal count
            if path.is_dir():
                return
            if should_skip(path):
                return
            zf.write(path, path.relative_to(ROOT).as_posix())
            count += 1

        for name in TOP_FILES:
            p = ROOT / name
            if p.exists():
                add(p)

        for name in TOP_DIRS:
            p = ROOT / name
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    add(f)
            elif p.is_file():
                add(p)

        # data/ 顶层保留项
        for name in DATA_KEEP:
            p = ROOT / "data" / name
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    add(f)
            elif p.is_file():
                add(p)

    size_mb = OUT.stat().st_size / 1048576
    print(f"打包完成: {OUT.name}  ({size_mb:.1f} MB, {count} 个文件)")
    print("拷贝到云电脑后解压，按 deploy/README_云电脑部署.md 操作。")
    return 0

if __name__ == "__main__":
    sys.exit(main())
