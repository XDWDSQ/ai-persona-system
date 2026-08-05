# -*- coding: utf-8 -*-
"""验证视觉链路：云端多模态优先（MiMo 看图），本地兜底，全失败 None。"""
import asyncio
import sys

sys.path.insert(0, r"D:\Users\31557\Desktop\AI拟人系统")

from server import ChatRequest, _try_vision_chat, _vision_mode, load_config  # noqa: E402


async def main() -> None:
    cfg = load_config()
    print("vision mode =", _vision_mode(cfg))
    print("cloud provider =", (cfg.get("cloud") or {}).get("provider"),
          "model =", (cfg.get("cloud") or {}).get("model"))

    img = "att_9b7986a2d48e.jpg"  # 263KB，用户真实上传过的图
    req = ChatRequest(
        message="这张图里有什么？用一句话简单说",
        attachments=[{"kind": "image", "name": img, "url": f"/uploads/{img}"}],
        history=[],
    )
    system = {"role": "system", "content": "你是大帅，回复简洁自然，一句话即可。"}
    raw = await _try_vision_chat(system, [], req, cfg)
    print("RESULT:", raw if raw else "None（视觉全不可用）")


if __name__ == "__main__":
    asyncio.run(main())
