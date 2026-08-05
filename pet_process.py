#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pet_process.py — 桌宠视频批量处理工具

功能：把"大帅X.mp4" 视频 → RGBA 帧序列 → 抠像去背景 → 按人物包围盒中心裁 1:1
     → 缩到 320px → 输出 transparent animated WebP（可无缝循环）

用法：
  python pet_process.py --all                 # 批量处理"大帅*.mp4"
  python pet_process.py 输入.mp4 --name happy # 处理单个（自定义状态名）
  python pet_process.py --all --fps 12 --size 320  # 调参

维护性：换视频 = 重跑命令；加情绪 = 文件名加一行映射 + 跑命令
"""
import argparse, glob, os, sys
import cv2
import numpy as np
from PIL import Image
from rembg import remove, new_session

# 文件名 → 状态名映射（以后加情绪在这里加一行即可）
NAME_MAP = {
    '空闲': 'idle', '开心': 'happy', '悲伤': 'sad',
    '难过': 'sad', '愤怒': 'angry', '生气': 'angry',
    '害羞': 'shy', '思考': 'thinking', '摸头': 'pat',
}

IN_DIR  = r'D:\Users\31557\Downloads'
OUT_DIR = r'D:\Users\31557\Desktop\AI拟人系统\xiaoni-ai-persona\pages\pet'

_session = None

def get_session():
    global _session
    if _session is None:
        print('[init] 加载抠像模型（首次会下载 u2net ~170MB）...', flush=True)
        _session = new_session('u2net')
    return _session

def remove_bg(rgb: np.ndarray, fast_size=512) -> np.ndarray:
    """RGB → RGBA（背景透明），先缩放提速再抠像"""
    h, w = rgb.shape[:2]
    scale = fast_size / min(h, w)
    if scale < 1:
        nh, nw = int(h * scale), int(w * scale)
        rgb_small = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
    else:
        rgb_small = rgb
        nh, nw = h, w
    rgba_small = remove(rgb_small, session=get_session(), only_mask=False)
    if scale < 1:
        rgba = cv2.resize(rgba_small, (w, h), interpolation=cv2.INTER_CUBIC)
        # 重采样可能让 alpha 边缘模糊，二值化一下
        a = rgba[..., 3]
        rgba[..., 3] = np.where(a > 128, 255, 0).astype(np.uint8)
        return rgba
    return rgba_small

def get_bbox(rgba: np.ndarray):
    a = rgba[..., 3]
    ys, xs = np.where(a > 128)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())

def parse_name(filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename))[0]  # "大帅空闲"
    for k, v in NAME_MAP.items():
        if k in base:
            return v
    # 兜底：去"大帅"前缀转拼音难，直接返回原 base
    return base.replace('大帅', '').strip() or 'unknown'

def process_one(in_path: str, name: str, out_dir: str,
                target_size=320, target_fps=12, margin=1.05):
    print(f'\n[{name}] 读取: {os.path.basename(in_path)}', flush=True)
    cap = cv2.VideoCapture(in_path)
    if not cap.isOpened():
        print(f'  × 打开失败（非 ASCII 路径？）', flush=True)
        return None
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 24
    step = max(round(src_fps / target_fps), 1)

    rgba_list = []
    bboxes = []
    idx = 0
    while True:
        ok, bgr = cap.read()
        if not ok: break
        if idx % step == 0:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            rgba = remove_bg(rgb)
            rgba_list.append(rgba)
            bb = get_bbox(rgba)
            if bb: bboxes.append(bb)
        idx += 1
    cap.release()
    if not rgba_list:
        print(f'  × 没读到帧', flush=True)
        return None

    # 全局人物 bbox
    x0 = min(b[0] for b in bboxes)
    y0 = min(b[1] for b in bboxes)
    x1 = max(b[2] for b in bboxes)
    y1 = max(b[3] for b in bboxes)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    half = max(x1 - x0, y1 - y0) // 2
    half = int(half * margin)  # 留点边距

    pil_frames = []
    h_src, w_src = rgba_list[0].shape[:2]
    for rgba in rgba_list:
        x_start = max(cx - half, 0)
        y_start = max(cy - half, 0)
        x_end   = min(cx + half, w_src)
        y_end   = min(cy + half, h_src)
        # 边界不够时用透明像素补齐（保证 1:1）
        cw, ch = x_end - x_start, y_end - y_start
        out = np.zeros((half * 2, half * 2, 4), dtype=np.uint8)
        # 居中粘贴
        px = (half * 2 - cw) // 2
        py = (half * 2 - ch) // 2
        out[py:py + ch, px:px + cw] = rgba[y_start:y_end, x_start:x_end]
        img = Image.fromarray(out, 'RGBA').resize(
            (target_size, target_size), Image.LANCZOS)
        pil_frames.append(img)

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'{name}.webp')
    duration = int(1000 / target_fps)
    pil_frames[0].save(
        out_path, 'WEBP', save_all=True, append_images=pil_frames[1:],
        duration=duration, loop=0, disposal=2, lossless=False, quality=85,
    )
    sz = os.path.getsize(out_path) // 1024
    print(f'  ✓ {len(pil_frames)} 帧, {duration}ms/帧, {sz}KB → {out_path}', flush=True)
    return out_path

def main():
    ap = argparse.ArgumentParser(description='桌宠视频 → 透明循环 WebP')
    ap.add_argument('input', nargs='?', help='输入 mp4 路径（单文件）')
    ap.add_argument('--name', help='状态名（如 idle/happy）')
    ap.add_argument('--all', action='store_true', help='批量处理 D:/Downloads/大帅*.mp4')
    ap.add_argument('--in-dir', default=IN_DIR)
    ap.add_argument('--out-dir', default=OUT_DIR)
    ap.add_argument('--fps', type=int, default=12)
    ap.add_argument('--size', type=int, default=320)
    ap.add_argument('--margin', type=float, default=1.05)
    args = ap.parse_args()

    tasks = []
    if args.all:
        for p in sorted(glob.glob(os.path.join(args.in_dir, '大帅*.mp4'))):
            tasks.append((p, parse_name(p)))
    elif args.input:
        if not args.name:
            print('单文件模式需要 --name 状态名'); sys.exit(1)
        tasks.append((args.input, args.name))

    if not tasks:
        print('没有任务。用 --all 或指定 input 文件。'); sys.exit(1)

    print(f'计划处理 {len(tasks)} 段：{ [n for _,n in tasks] }', flush=True)
    results = []
    for p, name in tasks:
        r = process_one(p, name, args.out_dir,
                        target_size=args.size, target_fps=args.fps,
                        margin=args.margin)
        if r: results.append(r)

    print(f'\n=== 完成 {len(results)}/{len(tasks)} ===', flush=True)
    for r in results:
        print('  ', r)

if __name__ == '__main__':
    main()