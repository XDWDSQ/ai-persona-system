# -*- coding: utf-8 -*-
"""生成公网可下载的部署包 cloud_deploy_public.zip（密钥脱敏版）。

与 pack_cloud.py 的区别：
- **不含任何 data/**：会话记录、角色记忆、用户上传照片、音色参考音频都是
  真实个人数据（含真人语音），公网可下载的包一律不带；解压后首次启动会自动
  建好空的 data/ 目录，用户从零开始自己的数据
- config.json 中一切凭据字段（access_token / *.api_key 及任意同名叶子）置空，保留占位
- 不打包 .env（真实密钥只在本地）；打包 .env.example 作为模板
- 不含 deploy/README_*（机主内部部署拓扑）
- 产出前跑 verify_public_package 自检，任一项泄漏直接构建失败

用法：python deploy/pack_public.py
下载后需要用户把本地 .env 内容复制到云电脑同名文件，并补 config.json 密钥。
"""
import json
import sys
import zipfile
from pathlib import Path
from pack_cloud import (
    ROOT,
    TOP_FILES,
    TOP_DIRS,
    PUBLIC_DATA_KEEP,
    blank_sensitive,
    should_skip,
    verify_public_package,
)

OUT = ROOT / "cloud_deploy_public.zip"


def main() -> int:
    if OUT.exists():
        OUT.unlink()
    count = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        def add(path: Path):
            nonlocal count
            if path.is_dir() or should_skip(path, public=True):
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
            blank_sensitive(cfg)
            zf.writestr("config.json", json.dumps(cfg, ensure_ascii=False, indent=2))
            count += 1

        for name in TOP_DIRS:
            p = ROOT / name
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    add(f)
            elif p.is_file():
                add(p)

        # 公网包不带任何真实数据：PUBLIC_DATA_KEEP 目前为空。
        # 将来若要放示例数据，必须先确认它是可公开的合成数据。
        for name in PUBLIC_DATA_KEEP:
            p = ROOT / "data" / name
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    add(f)
            elif p.is_file():
                add(p)

    size_mb = OUT.stat().st_size / 1048576
    print(f"脱敏包完成: {OUT.name}  ({size_mb:.1f} MB, {count} 个文件)")
    verify_public_package(OUT)  # 不通过则 SystemExit，不留下可发布的包
    print("config.json 凭据已置空，.env 与 data/ 未包含；下载后需自行补密钥与数据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
