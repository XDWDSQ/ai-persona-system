# -*- coding: utf-8 -*-
"""本地 LLM 上游获取器。

启动时自动检测：
  1. llama.cpp 运行时可执行文件缺失 → 从 GitHub releases 下载并解压到 llm/bin
  2. 模型文件缺失或不完整 → 从多镜像自动下载（hf-mirror → HF 官方 → ModelScope）

特性：断点续传（.part 临时文件）、镜像自动切换、失败重试。
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent            # llm/
BIN_DIR = BASE / "bin"
MODELS_DIR = BASE / "models"
SERVER_EXE = BIN_DIR / "llama-server.exe"
MODEL_FILE = MODELS_DIR / "Qwen3.5-4B-Q4_K_M.gguf"

CURL = r"C:\Windows\System32\curl.exe"
TAR = r"C:\Windows\System32\tar.exe"

LLAMA_ZIP_URL = (
    "https://github.com/ggml-org/llama.cpp/releases/download/"
    "b10227/llama-b10227-bin-win-cuda-12.4-x64.zip"
)
# CUDA 版标志文件：bin 里存在 ggml-cuda*.dll 才认为是 GPU 运行时
CUDA_MARKER = BIN_DIR / "ggml-cuda.dll"
MODEL_MIRRORS = [
    "https://hf-mirror.com/lmstudio-community/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf",
    "https://huggingface.co/lmstudio-community/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf",
    "https://modelscope.cn/models/Qwen/Qwen3.5-4B-GGUF/resolve/master/Qwen3.5-4B-Q4_K_M.gguf",
]
MODEL_MIN_SIZE = 2 << 30          # 4B Q4 约 2.6GB，低于 2GB 视为未下载完
ZIP_MIN_SIZE = 5 << 20            # 运行时 zip 约 17MB

# 可选完整性校验：填入十六进制 sha256 即强制校验，留空则只做大小闸门并明确告警。
# 此前唯一的"完整性"判断是文件字节数下限 —— 只要镜像（或被中间设备）给出的残缺/
# 替换产物超过下限就会被接受，zip 会被 tar 解开、里面的 exe 随后被直接执行。
EXPECTED_SHA256: dict[str, str] = {
    # "llama_zip": "...",
    # "model": "...",
}


def _human(size: int) -> str:
    return f"{size / (1 << 30):.2f}GB" if size >= (1 << 30) else f"{size / (1 << 20):.1f}MB"


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(url: str, dest: Path, min_size: int, what: str, sha_key: str = "") -> bool:
    """断点续传下载单个文件，返回是否成功。

    续传只能续同一个 URL 的断点：ensure_model 会在 hf-mirror / HF 官方 /
    ModelScope 之间切换，而各镜像对同一路径给出的字节并不保证一致（缓存版本、
    修订点都不同）。带着 A 镜像的 1.5GB 残片去 B 镜像 `-C -` 续传，拼出来是一个
    大小完全合法、内容却是混合体的 .gguf —— 大小闸门根本拦不住。所以换了 URL
    就必须丢弃 .part 重来。
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    marker = tmp.with_name(tmp.name + ".url")
    want = EXPECTED_SHA256.get(sha_key) if sha_key else None
    for attempt in range(1, 4):
        try:
            if marker.exists() and marker.read_text(encoding="utf-8").strip() != url:
                print("    切换下载源，丢弃上一源的断点文件")
                tmp.unlink(missing_ok=True)
        except OSError:
            tmp.unlink(missing_ok=True)
        print(f"    下载 {what}（第 {attempt} 次尝试）: {url}")
        r = subprocess.run(
            [CURL, "-fL", "--retry", "2", "-C", "-", "-o", str(tmp), url],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size >= min_size:
            if want:
                got = _sha256_of(tmp)
                if got.lower() != want.lower():
                    print(f"    ❌ 校验和不匹配（期望 {want[:12]}…，实际 {got[:12]}…），删除后重试")
                    tmp.unlink(missing_ok=True)
                    time.sleep(2)
                    continue
            tmp.replace(dest)
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass
            print(f"    ✅ {what} 下载完成 ({_human(dest.stat().st_size)}"
                  + ("，校验和已核对)" if want else "，未做完整性校验)"))
            return True
        if tmp.exists():
            try:
                marker.write_text(url, encoding="utf-8")
            except OSError:
                pass
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
    if not _fetch(LLAMA_ZIP_URL, zip_path, ZIP_MIN_SIZE, "llama.cpp 运行时", "llama_zip"):
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
    print("⬇️  模型缺失或不完整，从上游获取（约 2.6GB）...")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for url in MODEL_MIRRORS:
        if _fetch(url, MODEL_FILE, MODEL_MIN_SIZE, "Qwen3.5-4B 模型", "model"):
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
