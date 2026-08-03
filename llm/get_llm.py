# -*- coding: utf-8 -*-
"""本地 LLM 上游获取器。

启动时自动检测：
  1. llama.cpp 运行时可执行文件缺失 → 从 GitHub releases 下载并解压到 llm/bin
  2. 模型文件缺失或不完整 → 从多镜像自动下载（hf-mirror → HF 官方 → ModelScope）

特性：断点续传（.part 临时文件）、镜像自动切换、失败重试。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent            # llm/
BIN_DIR = BASE / "bin"
MODELS_DIR = BASE / "models"
SERVER_EXE = BIN_DIR / "llama-server.exe"
MODEL_FILE = MODELS_DIR / "Qwen3-4B-Q4_K_M.gguf"

CURL = r"C:\Windows\System32\curl.exe"
TAR = r"C:\Windows\System32\tar.exe"

LLAMA_ZIP_URL = (
    "https://github.com/ggml-org/llama.cpp/releases/download/"
    "b10227/llama-b10227-bin-win-cuda-12.4-x64.zip"
)
# CUDA 版标志文件：bin 里存在 ggml-cuda*.dll 才认为是 GPU 运行时
CUDA_MARKER = BIN_DIR / "ggml-cuda.dll"
MODEL_MIRRORS = [
    "https://hf-mirror.com/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf",
    "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf",
    "https://modelscope.cn/models/Qwen/Qwen3-4B-GGUF/resolve/master/Qwen3-4B-Q4_K_M.gguf",
]
MODEL_MIN_SIZE = 1 << 30          # 模型正常约 2.3GB，低于 1GB 视为未下载完
ZIP_MIN_SIZE = 5 << 20            # 运行时 zip 约 17MB


def _human(size: int) -> str:
    return f"{size / (1 << 30):.2f}GB" if size >= (1 << 30) else f"{size / (1 << 20):.1f}MB"


def _fetch(url: str, dest: Path, min_size: int, what: str) -> bool:
    """断点续传下载单个文件，返回是否成功。"""
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, 4):
        print(f"    下载 {what}（第 {attempt} 次尝试）: {url}")
        r = subprocess.run(
            [CURL, "-fL", "--retry", "2", "-C", "-", "-o", str(tmp), url],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size >= min_size:
            tmp.replace(dest)
            print(f"    ✅ {what} 下载完成 ({_human(dest.stat().st_size)})")
            return True
        if tmp.exists():
            print(f"    当前已下载 {_human(tmp.stat().st_size)}，准备重试")
        time.sleep(2)
    return False


def ensure_runtime() -> bool:
    if SERVER_EXE.exists() and CUDA_MARKER.exists():
        print("✅ llama.cpp GPU(CUDA) 运行时就绪")
        return True
    if SERVER_EXE.exists():
        # 已是 CPU 版（旧版本）→ 删除并换 CUDA 版
        print("⬇️  检测到 CPU 版运行时，删除并替换为 CUDA 版...")
        for f in BIN_DIR.glob("*"):
            try:
                f.unlink(missing_ok=True)
            except PermissionError:
                print(f"    ⚠️  {f.name} 被占用无法删除（llama-server 正在运行？请先关闭）")
        if SERVER_EXE.exists():
            print("❌ 部分文件被占用，请先停止本地模型服务（关闭 start_llm.bat 窗口）后重试")
            return False
    print("⬇️  llama.cpp 运行时可执行文件缺失，从上游获取...")
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = BASE / "llama.cpp.zip"
    if not _fetch(LLAMA_ZIP_URL, zip_path, ZIP_MIN_SIZE, "llama.cpp 运行时"):
        print("❌ llama.cpp 运行时下载失败，请检查网络后重试")
        return False
    print("    解压到 llm/bin ...")
    r = subprocess.run([TAR, "-xf", str(zip_path), "-C", str(BIN_DIR)],
                       capture_output=True, text=True)
    zip_path.unlink(missing_ok=True)
    if r.returncode != 0 or not SERVER_EXE.exists():
        print("❌ 解压失败，请手动从 https://github.com/ggml-org/llama.cpp/releases 获取")
        return False
    print("✅ llama.cpp 运行时就绪")
    return True


def ensure_model() -> bool:
    if MODEL_FILE.exists() and MODEL_FILE.stat().st_size >= MODEL_MIN_SIZE:
        print(f"✅ 模型就绪 ({_human(MODEL_FILE.stat().st_size)})")
        return True
    print("⬇️  模型缺失或不完整，从上游获取（约 2.3GB）...")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for url in MODEL_MIRRORS:
        if _fetch(url, MODEL_FILE, MODEL_MIN_SIZE, "Qwen3-4B 模型"):
            print(f"✅ 模型就绪 ({_human(MODEL_FILE.stat().st_size)})")
            return True
        print("    镜像不可用，切换下一个...")
    print("❌ 模型下载失败，请检查网络后重试")
    return False


def main() -> int:
    print("=== 本地 LLM 上游获取 ===")
    if not ensure_runtime() or not ensure_model():
        return 1
    print("=== 全部就绪 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
