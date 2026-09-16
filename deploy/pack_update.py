# -*- coding: utf-8 -*-
"""生成「代码更新包」update.zip：本地改完代码后，增量更新到云电脑。

只打包代码/前端/配置，**不含 data/ 与 .env** —— 云电脑上的会话记录、
角色记忆等数据是唯一真源，绝不能被打包覆盖。

用法（本地运行）：
    python deploy/pack_update.py

输出：
    update.zip（约几 MB）

云电脑上的操作：
    1. 用微信/共享盘把 update.zip 传到云电脑
    2. 解压到项目根目录，选「替换现有文件」（保留 data/ 不动）
    3. 重启 8000 服务
"""
import json
import sys
import zipfile
from pathlib import Path
from pack_cloud import ROOT, should_skip, blank_sensitive

# 公网模式：--public 时 config.json 密钥/口令脱敏（用于放到公网链接让云电脑下载）
PUBLIC = "--public" in sys.argv
OUT = ROOT / ("update_public.zip" if PUBLIC else "update.zip")

# 更新包内容：代码 + 前端 + 配置 + 脚本；不含 data/ .env .git
UPDATE_FILES = [
    "server.py",
    "role_engine.py",
    "minimax_llm.py",
    "story_kpl2027.py",
    "server_pkg",
    "requirements.txt",
    "config.json",        # 含访问口令；公网模式（--public）随密钥一并脱敏
    "config.example.json",
    ".env.example",
    "README.md",
    "minimax_clone.py",
    "verify_chain.py",
    "xiaoni-ai-persona",
    "docs-specs",
    "deploy",
]


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

        for name in UPDATE_FILES:
            p = ROOT / name
            if name == "config.json" and PUBLIC:
                cfg = json.loads(p.read_text(encoding="utf-8"))
                blank_sensitive(cfg)
                zf.writestr("config.json", json.dumps(cfg, ensure_ascii=False, indent=2))
                count += 1
            elif p.is_dir():
                for f in sorted(p.rglob("*")):
                    add(f)
            elif p.is_file():
                add(p)

    size_mb = OUT.stat().st_size / 1048576
    print(f"更新包完成: {OUT.name} ({size_mb:.1f} MB, {count} 个文件)")
    print("已排除: data/（云上数据唯一真源，不覆盖）、.env、.git、llm/")
    if PUBLIC:
        print("公网模式：config.json 密钥已脱敏（云端 key 靠云上 .env 提供）")
    print("传到云电脑后：解压替换 -> 重启服务")
    return 0


if __name__ == "__main__":
    sys.exit(main())
