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
import json
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
    "requirements-tools.txt",  # 桌宠素材加工工具依赖（服务端运行不需要）
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

# 公网包**不带任何 data/**：DATA_KEEP 是为私有云电脑副本设计的，里面装的是
# 真实聊天记录（sessions.json/memory/state/story）、用户发来的照片（uploads）、
# 以及真人语音（clone_ref，二十余 MB）——属于个人数据与生物特征信息，
# 一旦进了带公网下载链接的包就等于对外发布。全新解压后 server 启动会自动
# 建好 data/ 各子目录，因此公网包留空即可。
PUBLIC_DATA_KEEP: list[str] = []

# 公网包额外排除：内部部署拓扑（仓库地址、webhook 端口与自启流程）
PUBLIC_SKIP_PREFIXES = (
    "deploy/README_",
)

# config.json 里已知需要脱敏的具体路径（仅作文档与自检清单；实际脱敏按
# SENSITIVE_LEAVES 的叶子名递归匹配，避免新增 provider / 新配置分支时漏掉）。
# access_token 是系统访问口令，和各 LLM/TTS 密钥一样必须从公网包剥离。
SENSITIVE_PATHS = [
    "access_token",
    "cloud.api_key",
    "cloud_providers.mimo.api_key",
    "cloud_providers.deepseek.api_key",
    "cloud_providers.ark.api_key",
    "cloud_providers.minimax.api_key",
    "cloud_providers.custom.api_key",
    "voice.aliyun.api_key",
    "voice.minimax.api_key",
    "voice.mimo.api_key",
]

# 按叶子名脱敏：SENSITIVE_PATHS 是枚举式白名单，用户在设置页新增任意
# cloud_provider（如 openrouter）或填了 local.api_key 时不在名单里，
# 真实密钥就会原样打进公网包。凭据类字段一律按名字兜住。
# 叶子名集合与 /api/status 的脱敏同源（server_pkg.text_utils），避免两处漂移；
# 独立运行打包脚本时（sys.path 里没有项目根）回退到本地副本。
try:
    sys.path.insert(0, str(ROOT))
    from server_pkg.text_utils import SENSITIVE_LEAVES  # noqa: E402
except ImportError:  # pragma: no cover
    SENSITIVE_LEAVES = {
        "api_key", "apikey", "access_key", "secret_key", "app_secret",
        "access_token", "refresh_token", "token", "token_plan_api_key",
        "password", "passwd", "secret", "webhook_secret",
    }
# 注意是「全等」而非「包含」——roles.*.news.keyword 之类不是凭据。


def _is_sensitive_leaf(key: str) -> bool:
    return isinstance(key, str) and key.strip().lower() in SENSITIVE_LEAVES


def blank_sensitive(obj, path: str = "") -> None:
    """递归清空一切凭据字段（公网脱敏包用，原地修改）。

    既覆盖 SENSITIVE_PATHS 里的已知位置，也覆盖任意深度的同名叶子；
    字符串置空，列表里的字符串元素同样置空。
    """
    if not isinstance(obj, dict):
        return
    for key, val in list(obj.items()):
        cur = f"{path}.{key}" if path else key
        if _is_sensitive_leaf(key) or cur in SENSITIVE_PATHS:
            if isinstance(val, str):
                obj[key] = ""
            elif isinstance(val, list):
                obj[key] = ["" if isinstance(v, str) else v for v in val]
            continue
        if isinstance(val, dict):
            blank_sensitive(val, cur)
        elif isinstance(val, list):
            for item in val:
                blank_sensitive(item, cur)


# 非机密哨兵值：这些字符串出现在凭据字段里不代表泄露，而是「本就不需要密钥」的
# 约定写法。server.py 里 `api_key or "none"` 与掩码回填都依赖它们，所以自检必须
# 把它们当成空值，否则 config.example.json 这类模板会把构建卡死。
_SECRET_PLACEHOLDERS = {"", "none", "null", "changeme", "your_api_key", "sk-xxx", "sk-xxxx"}


def _is_blank_secret(val) -> bool:
    if not isinstance(val, str):
        return True  # 非字符串（嵌套 dict/list）由递归另行处理
    s = val.strip()
    return not s or s.lower() in _SECRET_PLACEHOLDERS or s.startswith("***")


def find_leaked_secrets(zf: zipfile.ZipFile) -> list[str]:
    """扫包内 JSON：返回仍带着非空凭据的「成员!路径」列表（不返回其值）。"""
    problems: list[str] = []
    for name in zf.namelist():
        if not name.lower().endswith((".json", ".example")):
            continue
        try:
            data = json.loads(zf.read(name).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for path, val in _iter_sensitive(data):
            if isinstance(val, str) and not _is_blank_secret(val):
                problems.append(f"{name}!{path}")
    return problems


def _iter_sensitive(obj, path: str = ""):
    if isinstance(obj, dict):
        for key, val in obj.items():
            cur = f"{path}.{key}" if path else key
            if _is_sensitive_leaf(key):
                yield cur, val
            yield from _iter_sensitive(val, cur)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from _iter_sensitive(item, f"{path}[{i}]")


_TEXT_SUFFIXES = (".md", ".txt", ".py", ".js", ".json", ".html", ".bat", ".ps1",
                  ".yaml", ".yml", ".css", ".example", ".webmanifest", ".ini", ".cfg")

# 需要参与「字面量扫描」的凭据值：从 .env 与 config.json 的凭据字段取真值。
# 只按字段结构扫 JSON 是不够的 —— 仓库里的 Markdown 文档、脚注、示例代码
# 都可能把口令或 key 原样抄进去（实测确实抄过一份历史弱口令），而这些是纯文本，
# find_leaked_secrets 那种结构化扫描根本看不到它们。
_MIN_SECRET_LITERAL_LEN = 5


def collect_secret_literals(root: Path) -> dict[str, str]:
    """收集本机真实凭据值 -> 来源标签。只返回给自检用，绝不打印值本身。"""
    out: dict[str, str] = {}

    def add(value, label: str):
        if isinstance(value, str):
            v = value.strip()
            if len(v) >= _MIN_SECRET_LITERAL_LEN and not v.startswith("***"):
                out[v] = label

    env_path = root / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    add(v.strip().strip('"').strip("'"), f".env:{k.strip()}")
        except OSError:
            pass
    cfg_path = root / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            for path, val in _iter_sensitive(cfg):
                add(val, f"config.json:{path}")
        except (OSError, json.JSONDecodeError):
            pass
    return out


def find_leaked_literals(zf: zipfile.ZipFile, literals: dict[str, str]) -> list[str]:
    """全包文本成员做字面量匹配，返回「成员(命中的来源标签)」列表（不含值）。"""
    problems: list[str] = []
    if not literals:
        return problems
    for name in zf.namelist():
        if not name.lower().endswith(_TEXT_SUFFIXES):
            continue
        try:
            text = zf.read(name).decode("utf-8", "ignore")
        except (KeyError, zipfile.BadZipFile):
            continue
        hit_labels = sorted({label for secret, label in literals.items() if secret in text})
        if hit_labels:
            problems.append(f"{name} <- {', '.join(hit_labels)}")
    return problems


def verify_public_package(out: Path, root: Path | None = None) -> None:
    """公网包出厂自检：个人数据与凭据只要漏进去就直接让构建失败。

    打包脚本曾经把 data/（真实聊天记录、照片、真人语音）原样打进可公网
    下载的包里，而构建日志只显示「密钥已置空」。这里做结构性拦截：
    宁可构建报错，也不能把用户数据发布出去。
    """
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        leaked_data = [n for n in names if n == "data" or n.startswith("data/")]
        leaked_env = [n for n in names if n.split("/")[-1] in (".env",)]
        leaked_docs = [n for n in names if n.startswith(PUBLIC_SKIP_PREFIXES)]
        leaked_secrets = find_leaked_secrets(zf)
        leaked_literals = find_leaked_literals(zf, collect_secret_literals(root or ROOT))
    errors: list[str] = []
    if leaked_data:
        errors.append(f"{len(leaked_data)} 个 data/ 成员（真实用户数据）: {leaked_data[:5]}")
    if leaked_env:
        errors.append(f".env 未排除: {leaked_env}")
    if leaked_docs:
        errors.append(f"内部部署文档未排除: {leaked_docs[:5]}")
    if leaked_secrets:
        errors.append(f"{len(leaked_secrets)} 个凭据字段非空（值不外泄，只报路径）: {leaked_secrets[:10]}")
    if leaked_literals:
        errors.append(f"{len(leaked_literals)} 个文本成员原样携带本机凭据值（只报来源，不报值）: "
                      f"{leaked_literals[:8]}")
    if errors:
        raise SystemExit(
            "[X] 公网包自检未通过，拒绝产出可下载包：\n  - " + "\n  - ".join(errors)
        )
    print(f"公网包自检通过：无 data/ 成员、无凭据字段、无凭据字面量（共 {len(names)} 个文件）")

def rel_parts(p: Path) -> list:
    return p.relative_to(ROOT).parts

def should_skip(p: Path, public: bool = False) -> bool:
    parts = rel_parts(p)
    if any(part in ("__pycache__", ".git", ".workbuddy", "node_modules") for part in parts):
        return True
    if parts and parts[0] == "llm":
        return True
    if public and "/".join(parts).startswith(PUBLIC_SKIP_PREFIXES):
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
