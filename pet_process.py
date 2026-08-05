#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pet_process.py — 桌宠视频批量处理工具（v2）

mp4 → 语义抠像(onnxruntime 直跑 ONNX，无需 rembg/cv2) → 软 alpha + 对比曲线 + 羽化
    → 时域平滑抑制帧间闪烁 → 全局人物包围盒 1:1 裁切 → LANCZOS 缩放
    → 透明动画 WebP（可无缝循环）

v2 相对 v1 的改进：
- 去掉 rembg/cv2 依赖：onnxruntime 直接推理（PyAV 解码 + PIL/numpy），环境更轻
- 软 alpha：不再二值化硬边，曲线+羽化让发丝/肩部边缘自然
- 时域平滑：低分辨率 mask 做 3 帧加权平均，抑制逐帧抠像抖动
- 默认模型 isnet-general-use（边缘更干净），u2net 可回退
- WebP 编码 method=6 + 质量可调，同尺寸体积通常比 v1 小一半以上

用法：
  python pet_process.py --all                          # 批量处理 桌面宠素材/大帅*.mp4
  python pet_process.py 输入.mp4 --name happy          # 单段
  python pet_process.py --all --model u2net --size 256 # 调参
"""
import argparse, glob, os, sys, time
import numpy as np
from PIL import Image, ImageFilter
import av
import onnxruntime as ort

# 文件名 → 状态名映射（以后加情绪在这里加一行即可）
NAME_MAP = {
    '空闲': 'idle', '开心': 'happy', '悲伤': 'sad',
    '难过': 'sad', '愤怒': 'angry', '生气': 'angry',
    '害羞': 'shy', '思考': 'thinking', '摸头': 'pat',
}

BASE = os.path.dirname(os.path.abspath(__file__))
IN_DIR  = os.path.join(BASE, '桌面宠素材')
OUT_DIR = os.path.join(BASE, 'xiaoni-ai-persona', 'pages', 'pet')
MODEL_DIR = os.path.expanduser('~/.u2net')
MODELS = {
    'isnet': 'isnet-general-use.onnx',
    'u2net': 'u2net.onnx',
}
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD  = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class MattingSession:
    """轻量 ONNX 抠像：自动适配输入尺寸，输出软 mask（0..1，模型原生分辨率）"""

    def __init__(self, model_name='isnet'):
        path = os.path.join(MODEL_DIR, MODELS[model_name])
        if not os.path.exists(path):
            print(f'× 模型不存在: {path}', flush=True)
            print('  下载: https://github.com/danielgatis/rembg/releases/download/v0.0.0/'
                  + MODELS[model_name], flush=True)
            sys.exit(1)
        t0 = time.time()
        self.sess = ort.InferenceSession(path, providers=['CPUExecutionProvider'])
        shape = self.sess.get_inputs()[0].shape          # [1,3,H,W]
        self.h, self.w = int(shape[2]), int(shape[3])
        self.in_name = self.sess.get_inputs()[0].name
        print(f'[init] {model_name} 加载完成 {self.w}x{self.h} ({time.time()-t0:.1f}s)', flush=True)

    def mask(self, rgb: Image.Image) -> np.ndarray:
        """PIL RGB → float32 mask (h,w) 0..1（模型原生分辨率）"""
        small = rgb.resize((self.w, self.h), Image.LANCZOS)
        arr = np.asarray(small, dtype=np.float32) / 255.0
        arr = (arr - MEAN) / STD
        out = self.sess.run(None, {self.in_name: arr.transpose(2, 0, 1)[None]})[0]
        m = out[0, 0]
        lo, hi = float(m.min()), float(m.max())
        if hi - lo < 1e-6:
            return np.zeros_like(m, dtype=np.float32)
        return ((m - lo) / (hi - lo)).astype(np.float32)


def refine_alpha(mask: np.ndarray, lo: float, hi: float, feather: float) -> np.ndarray:
    """对比曲线去半透明雾边 + 高斯羽化；输入输出均为 0..255 uint8 全分辨率 mask"""
    a = np.clip((mask - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    img = Image.fromarray((a * 255).astype(np.uint8))
    if feather > 0:
        img = img.filter(ImageFilter.GaussianBlur(feather))
    return np.asarray(img, dtype=np.float32) / 255.0


def get_bbox(alpha: np.ndarray, thr=0.5):
    ys, xs = np.where(alpha > thr)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def parse_name(filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename))[0]
    for k, v in NAME_MAP.items():
        if k in base:
            return v
    return base.replace('大帅', '').strip() or 'unknown'


def process_one(in_path, name, out_dir, session,
                target_size=320, target_fps=12, margin=1.05,
                alpha_lo=0.30, alpha_hi=0.80, feather=0.8, quality=80):
    print(f'\n[{name}] 读取: {os.path.basename(in_path)}', flush=True)
    container = av.open(in_path)
    stream = container.streams.video[0]
    src_fps = float(stream.average_rate or 24)
    step = max(round(src_fps / target_fps), 1)

    # ---- 第一遍：抽帧 + 低分辨率 mask + 时域平滑 ----
    rgb_list, mask_list = [], []
    idx = 0
    t0 = time.time()
    for frame in container.decode(video=0):
        if idx % step == 0:
            rgb_list.append(frame.to_image().convert('RGB'))
            mask_list.append(session.mask(rgb_list[-1]))
        idx += 1
    container.close()
    n = len(mask_list)
    if n == 0:
        print('  × 没读到帧', flush=True)
        return None
    print(f'  抽帧 {n} 张, 推理 {time.time()-t0:.0f}s', flush=True)

    # 时域平滑（3 帧加权），只在模型分辨率上做，内存小
    if n >= 3:
        sm = [mask_list[0]]
        for i in range(1, n - 1):
            sm.append(0.25 * mask_list[i - 1] + 0.5 * mask_list[i] + 0.25 * mask_list[i + 1])
        sm.append(mask_list[-1])
        mask_list = sm

    # ---- 全分辨率软 alpha + 全局 bbox ----
    bboxes = []
    alpha_full = []
    for rgb, m in zip(rgb_list, mask_list):
        w, h = rgb.size
        full = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR),
                          dtype=np.float32) / 255.0
        a = refine_alpha(full, alpha_lo, alpha_hi, feather)
        alpha_full.append(a)
        bb = get_bbox(a)
        if bb:
            bboxes.append(bb)
    if not bboxes:
        print('  × 全程没抠到人', flush=True)
        return None

    x0 = min(b[0] for b in bboxes); y0 = min(b[1] for b in bboxes)
    x1 = max(b[2] for b in bboxes); y1 = max(b[3] for b in bboxes)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    half = int(max(x1 - x0, y1 - y0) // 2 * margin)
    print(f'  全局 bbox=({x0},{y0})-({x1},{y1}) half={half}', flush=True)

    # ---- 1:1 裁切（边界不足补透明）→ 缩放 ----
    pil_frames = []
    for rgb, a in zip(rgb_list, alpha_full):
        w, h = rgb.size
        xs, ys = max(cx - half, 0), max(cy - half, 0)
        xe, ye = min(cx + half, w), min(cy + half, h)
        cw, ch = xe - xs, ye - ys
        rgba = np.zeros((half * 2, half * 2, 4), dtype=np.uint8)
        px, py = (half * 2 - cw) // 2, (half * 2 - ch) // 2
        region = np.asarray(rgb, dtype=np.uint8)[ys:ye, xs:xe]
        rgba[py:py + ch, px:px + cw, :3] = region
        rgba[py:py + ch, px:px + cw, 3] = (a[ys:ye, xs:xe] * 255).astype(np.uint8)
        pil_frames.append(Image.fromarray(rgba, 'RGBA').resize(
            (target_size, target_size), Image.LANCZOS))

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'{name}.webp')
    duration = int(1000 / target_fps)
    t0 = time.time()
    pil_frames[0].save(
        out_path, 'WEBP', save_all=True, append_images=pil_frames[1:],
        duration=duration, loop=0, disposal=2, lossless=False,
        quality=quality, method=6,
    )
    sz = os.path.getsize(out_path) // 1024
    print(f'  ✓ {n} 帧, {duration}ms/帧, {sz}KB, 编码 {time.time()-t0:.0f}s → {out_path}', flush=True)
    return out_path


def main():
    ap = argparse.ArgumentParser(description='桌宠视频 → 透明循环 WebP（v2）')
    ap.add_argument('input', nargs='?', help='输入 mp4 路径（单文件）')
    ap.add_argument('--name', help='状态名（如 idle/happy）')
    ap.add_argument('--all', action='store_true', help='批量处理 in-dir/大帅*.mp4')
    ap.add_argument('--in-dir', default=IN_DIR)
    ap.add_argument('--out-dir', default=OUT_DIR)
    ap.add_argument('--model', choices=sorted(MODELS), default='isnet',
                    help='抠像模型（默认 isnet，边缘更干净）')
    ap.add_argument('--fps', type=int, default=12)
    ap.add_argument('--size', type=int, default=320)
    ap.add_argument('--margin', type=float, default=1.05)
    ap.add_argument('--alpha-lo', type=float, default=0.30, help='alpha 曲线下限（去雾边）')
    ap.add_argument('--alpha-hi', type=float, default=0.80, help='alpha 曲线上限')
    ap.add_argument('--feather', type=float, default=0.8, help='边缘羽化半径(px)')
    ap.add_argument('--quality', type=int, default=80, help='WebP 质量')
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

    session = MattingSession(args.model)
    print(f'计划处理 {len(tasks)} 段：{[n for _, n in tasks]}', flush=True)
    results = []
    for p, name in tasks:
        r = process_one(p, name, args.out_dir, session,
                        target_size=args.size, target_fps=args.fps, margin=args.margin,
                        alpha_lo=args.alpha_lo, alpha_hi=args.alpha_hi,
                        feather=args.feather, quality=args.quality)
        if r:
            results.append(r)
    print(f'\n=== 完成 {len(results)}/{len(tasks)} ===', flush=True)
    for r in results:
        print('  ', r)


if __name__ == '__main__':
    main()
