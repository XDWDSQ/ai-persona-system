# -*- coding: utf-8 -*-
"""生成公网可下载的部署包 cloud_deploy_public.zip（密钥脱敏版）。

与 pack_cloud.py 的区别：
- config.json 中所有云端 API 密钥（cloud.api_key / cloud_providers.*.api_key /
  voice.aliyun.api_key / voice.minimax.api_key）置空，保留占位
- 不打包 .env（真实密钥只在本地）；打包 .env.example 作为模板
- 其余内容与 cloud_deploy.zip 一致

用法：python deploy/pack_public.py
下载后需要用户把本地 .env 内容复制到云电脑同名文件，并补 config.json 密钥。
"""
import json
import sys
import zipfile
from pathlib import Path
from pack_cloud import ROOT, TOP_FILES, TOP_DIRS, DATA_KEEP, DATA_SKIP, should_skip

OUT = ROOT / "cloud_deploy_public.zip"

# config.json 中需要脱敏的路径（点分路径）
SENSITIVE_PATHS = [
    "cloud.api_key",
    "cloud_providers.mimo.api_key",
    "cloud_providers.deepseek.api_key",
    "cloud_providers.ark.api_key",
    "cloud_providers.custom.api_key",
    "voice.aliyun.api_key",
    "voice.minimax.api_key",
]


def _blank_keys(obj: dict, path: str = "") -> None:
    """递归把 SENSITIVE_PATHS 对应的值置空。"""
    if not isinstance(obj, dict):
        return
    for key, val in obj.items():
        cur = f"{path}.{key}" if path else key
        if cur in SENSITIVE_PATHS and isinstance(val, str):
            obj[key] = ""
        elif isinstance(val, dict):
            _blank_keys(val, cur)


def main() -> int:
    if OUT.exists():
        OUT.unlink()
    count = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        def add(path: Path):
            nonlocal count
            if path.is_dir() or should_skip(path):
                return
            zf.write(path, path.relative_to(ROOT).as_posix())
            count += 1

        for name in TOP_FILES:
            p = ROOT / name
            if name in (".env", "config.json") or not p.exists():
                continue  # .env 不打包；config.json 脱敏后单独写入
            add(p)

        # config.json 脱敏后单独写入
        cfg_path = ROOT / "config.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            _blank_keys(cfg)
            zf.writestr("config.json", json.dumps(cfg, ensure_ascii=False, indent=2))
            count += 1

        for name in TOP_DIRS:
            p = ROOT / name
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    add(f)
            elif p.is_file():
                add(p)

        for name in DATA_KEEP:
            p = ROOT / "data" / name
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    add(f)
            elif p.is_file():
                add(p)

    size_mb = OUT.stat().st_size / 1048576
    print(f"脱敏包完成: {OUT.name}  ({size_mb:.1f} MB, {count} 个文件)")
    print("注意：config.json 密钥已置空，.env 未包含；下载后需补密钥。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
