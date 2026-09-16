# -*- coding: utf-8 -*-
"""从 update.zip 生成公网可下载的脱敏更新包 update_public.zip。

把 config.json 中所有云端 API 密钥与访问口令置空（云上靠 .env 回退机制工作），
其余文件原样保留。用于通过公网下载链接让云电脑 AI 自行下载更新。
"""
import json
import sys
import zipfile
from pathlib import Path

from pack_cloud import ROOT, blank_sensitive

SRC = ROOT / "update.zip"
DST = ROOT / "update_public.zip"


def main() -> int:
    if not SRC.exists():
        print("[X] 请先在本地运行 deploy/pack_update.py 生成 update.zip")
        return 1
    if DST.exists():
        DST.unlink()
    with zipfile.ZipFile(SRC) as zin, zipfile.ZipFile(DST, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "config.json":
                cfg = json.loads(data.decode("utf-8"))
                blank_sensitive(cfg)
                data = json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
            zout.writestr(info, data)
    print(f"脱敏更新包完成: {DST.name} ({DST.stat().st_size/1048576:.2f} MB)")
    print("config.json 密钥与访问口令已置空；云上靠 .env 回退，功能不受影响。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
