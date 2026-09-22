# -*- coding: utf-8 -*-
"""从 update.zip 生成公网可下载的脱敏更新包 update_public.zip。

把 config.json 中所有云端 API 密钥与访问口令置空（云上靠 .env 回退机制工作），
其余文件原样保留。用于通过公网下载链接让云电脑 AI 自行下载更新。
"""
import json
import sys
import zipfile
from pathlib import Path

from pack_cloud import (
    ROOT,
    PUBLIC_SKIP_PREFIXES,
    blank_sensitive,
    verify_public_package,
)

SRC = ROOT / "update.zip"
DST = ROOT / "update_public.zip"


def main() -> int:
    if not SRC.exists():
        print("[X] 请先在本地运行 deploy/pack_update.py 生成 update.zip")
        return 1
    if DST.exists():
        DST.unlink()
    dropped = 0
    with zipfile.ZipFile(SRC) as zin, zipfile.ZipFile(DST, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            name = info.filename
            # update.zip 是私有包：转公网包时必须剔掉个人数据与内部部署文档
            if name == "data" or name.startswith("data/") or name.startswith(PUBLIC_SKIP_PREFIXES):
                dropped += 1
                continue
            data = zin.read(name)
            if name.endswith(".json"):
                try:
                    cfg = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    cfg = None
                if isinstance(cfg, dict):
                    blank_sensitive(cfg)
                    data = json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
            zout.writestr(info, data)
    print(f"脱敏更新包完成: {DST.name} ({DST.stat().st_size/1048576:.2f} MB)，剔除 {dropped} 个成员")
    verify_public_package(DST)  # 不通过则 SystemExit，不留下可发布的包
    print("config.json 凭据已置空；云上靠 .env 回退，功能不受影响。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
