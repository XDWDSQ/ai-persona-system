# 大帅桌宠 · 情绪视频素材

存放 AI 视频生成的原始素材，避免散落在下载文件夹里丢失。

## 文件清单

| 文件 | 对应状态 | 用途 |
|------|---------|------|
| `pet_ref.jpg` | — | 图生视频参考图（喂给 AI 视频工具用） |
| `大帅空闲.mp4` | idle | 平静待机，循环 5 秒 |
| `大帅开心.mp4` | happy | 灿烂大笑 |
| `大帅悲伤.mp4` | sad | 低头叹气 |
| `大帅愤怒.mp4` | angry | 生气的状态 |
| `大帅害羞.mp4` | shy | 摸后脑勺，脸微红 |
| `大帅思考.mp4` | thinking | 歪头点下巴 |
| `大帅摸头.mp4` | pat | 闭眼摸头 |

## 生成要求（喂给 AI 视频工具的提示词模板）

每段生成时固定上传 `pet_ref.jpg` 作参考图，提示词统一开头：
> 图中这位男生保持参考图中的形象与坐姿，正面半身机位，人物居中，固定镜头无运动，背景替换为纯绿色幕布，柔和自然光，电影级写实质感。

末尾追加情绪动作 + 循环 5 秒 + "不要挑眉，不要歪嘴"。

完整 7 段提示词见 `../docs-specs/` 或对话历史。

## 换视频/加情绪的处理流程

```bash
# 1. 把新视频放入本目录（建议同名覆盖；新情绪取新名）
# 2. 单段重跑（覆盖 pages/pet/<state>.webp）：
python ../pet_process.py 本目录/新视频.mp4 --name <state>

# 批量重新处理全部（v2 默认 256px/10fps/q70，单段约 1 分钟）：
python ../pet_process.py --all

# 3. 重要：把 pages/pet.js 里的 ASSET_VERSION 加 1
#    （webp 走 7 天长缓存，不 bump 版本号用户会一直看到旧素材）

# 4. 重新打包：
python ../deploy/pack_update.py && python ../deploy/pack_update.py --public
```

v2 脚本说明：不依赖 rembg/cv2，用 onnxruntime 直跑 ~/.u2net/u2net.onnx
（PyAV 解码 + PIL/numpy 合成）。常用调参：--size 320（更清晰但更大）、
--alpha-lo/--alpha-hi（边缘松紧）、--feather（羽化）、--quality。

## 状态名中英文映射（处理脚本内置）

```
空闲→idle, 开心→happy, 悲伤/难过→sad,
愤怒/生气→angry, 害羞→shy, 思考→thinking, 摸头→pat
```

如需新情绪：①文件命名带中文状态名（脚本自动映射）②`pet.js` 里 `PET_META` 加一行配置。