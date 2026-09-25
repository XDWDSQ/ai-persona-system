#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
pet_process.py — 桌宠视频批量处理工具（v3：画质 + 抠图优化）

mp4 → 语义抠像(onnxruntime 直跑 ONNX，无需 rembg/cv2) → 软 alpha + 对比曲线 + 羽化
    → 时域平滑抑制帧间闪烁 → 全局人物包围盒 1:1 裁切 → 边缘去色边(defringe) + 蚀刻
    → LANCZOS 缩放 → 透明动画 WebP（可无缝循环）

v3 相对 v2 的改进（画质 / 抠图）：
- 抠图：新增 颜色去污染(defringe) —— 半透明边缘像素的 RGB 向局部前景色收敛，
  消除绿幕残留导致的 绿边/白边 与 发丝半透明灰化（数学：blur(rgb·a)/blur(a) 反演前景色）
- 抠图：新增 alpha 蚀刻(shrink) —— 腐蚀最外层 1px 半透明杂边，收掉肩部/发梢的亮边
- 抠图：alpha 对比曲线下限下调 0.45→0.40，发丝/细节过渡保留更多半透明层次
- 画质：默认输出 256px → 384px（高 DPR 屏更锐利），WebP 质量 70 → 82，
  羽化 0.8 → 0.9（高分辨率下边缘过渡更顺滑）；仍可用 --size 512 出高清档
- 兼容：全部旧参数名保留，显式传参行为不变

用法：
  python pet_process.py --all                          # 批量处理 桌面宠素材/大帅*.mp4
  python pet_process.py 输入.mp4 --name happy          # 单段
  python pet_process.py --all --size 512 --quality 90  # 高清档
  python pet_process.py --all --defringe 0 --shrink 0  # 关闭新抠图优化（对比用）
"""
import argparse, glob, math, os, sys, time
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

    def __init__(self, model_name='u2net'):
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


def defringe_rgba(rgba: np.ndarray, strength: float = 0.85, sigma: float = 1.6) -> np.ndarray:
    """边缘颜色去污染（v3 核心）：消除绿幕残留的绿边/白边与发丝半透明灰化。

    原理：假设边缘像素颜色 = 前景色·α + 背景色·(1-α)。
    用 alpha 加权高斯模糊估计局部前景色 inner = blur(rgb·α) / blur(α)，
    再把半透明像素(0.03<α<0.97)的 RGB 按 (1-α)·strength 的权重向 inner 收敛。
    越透明的像素（越接近纯背景）越彻底地替换为前景色，白/绿边被消化；
    全不透明内部不动，完全透明外部不动。
    """
    a = rgba[..., 3].astype(np.float32) / 255.0
    rgb = rgba[..., :3].astype(np.float32)

    # blur(rgb·α)：把预乘后的图像分通道高斯模糊
    premul = rgb * a[..., None]
    premul_img = Image.fromarray(np.clip(premul, 0, 255).astype(np.uint8))
    blurred = np.stack([
        np.asarray(premul_img.getchannel(c).filter(ImageFilter.GaussianBlur(sigma)),
                   dtype=np.float32) for c in range(3)
    ], axis=-1)
    # blur(α)
    blurred_a = np.asarray(
        Image.fromarray((a * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(sigma)),
        dtype=np.float32) / 255.0
    # 局部前景色估计（低 α 区域除法噪声大，clamp 保稳定）
    inner = np.divide(blurred, np.maximum(blurred_a[..., None], 1e-3))

    # 收敛权重随透明度增加：全不透明 w=0 完全不动，全透明 w≈1（α=0 不可见，无影响）
    w = np.clip((1.0 - a) * strength, 0.0, 1.0)[..., None]
    out = rgba.copy()
    out[..., :3] = np.clip(rgb * (1.0 - w) + inner * w, 0, 255).astype(np.uint8)
    return out


def shrink_alpha(alpha: np.ndarray, px: int = 1) -> np.ndarray:
    """alpha 蚀刻：腐蚀最外层 px 像素的半透明环，收掉肩部/发梢残留的 1px 亮边。
    px=0 关闭。输入输出均为 0..1 float32 全分辨率 mask。"""
    if px <= 0:
        return alpha
    img = Image.fromarray((alpha * 255).astype(np.uint8))
    eroded = np.asarray(img.filter(ImageFilter.MinFilter(px * 2 + 1)), dtype=np.float32) / 255.0
    # 只把"原来半透明、腐蚀后为 0"的最外层杂边去掉，内部渐变保留
    ring = (alpha > 0.0) & (eroded <= 0.005)
    alpha = alpha.copy()
    alpha[ring] = 0.0
    return alpha


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
                target_size=384, target_fps=10, margin=1.05,
                alpha_lo=0.40, alpha_hi=0.90, feather=0.9,
                defringe=2, shrink=1, quality=82, max_frames=600):
    """单段处理；任何失败返回 None（批量模式不中断其他段）。"""
    try:
        return _process_one_impl(in_path, name, out_dir, session,
                                 target_size, target_fps, margin,
                                 alpha_lo, alpha_hi, feather,
                                 defringe, shrink, quality, max_frames)
    except Exception as exc:  # noqa: BLE001
        print(f'  × [{name}] 处理失败: {exc}', flush=True)
        return None


def _process_one_impl(in_path, name, out_dir, session,
                      target_size=384, target_fps=10, margin=1.05,
                      alpha_lo=0.40, alpha_hi=0.90, feather=0.9,
                      defringe=2, shrink=1, quality=82, max_frames=600):
    print(f'\n[{name}] 读取: {os.path.basename(in_path)}', flush=True)

    # ---- 第一遍：只保留低分辨率 mask，算全局 bbox ----
    # 注意这里**不是**"内存恒定"：mask_list 随帧数线性增长
    # （u2net 320² float32 ≈ 0.41MB/帧，isnet 1024² ≈ 4.2MB/帧）。所以用 max_frames
    # 兜住上限 —— 3 分钟素材按 10fps 就是 1800 帧，isnet 下光 mask 就要 7GB+，
    # 结果是进程被杀、几分钟推理全白跑（这是个离线加工工具，OOM 只影响你自己，但很气人）。
    container = av.open(in_path)
    try:
        stream = container.streams.video[0]
        W, H = stream.width, stream.height
        tb = stream.time_base          # 必须在 close 前缓存（close 后旧 stream 属性失效）
        interval = 1.0 / target_fps
        next_t, mask_list, k = 0.0, [], 0
        kept_ts = []                   # 实际保留帧的时间戳（算真实帧间隔用）
        t0 = time.time()
        for frame in container.decode(video=0):
            t = float(frame.pts * tb) if frame.pts is not None else k / target_fps
            if t < next_t - 1e-6:
                continue
            mask_list.append(session.mask(frame.to_image().convert('RGB')))
            kept_ts.append(t)
            k += 1
            if len(mask_list) >= max_frames:
                print(f'  ! 已达 --max-frames={max_frames}，后面的帧不再抽取'
                      f'（素材过长；成品会短于源视频，想要更长请显式调大该值）', flush=True)
                break
            while next_t <= t + 1e-6:
                next_t += interval
    finally:
        # 异常路径也必须 close：否则 Windows 上输入文件被句柄锁定，且解码缓冲累积
        container.close()
    n = len(mask_list)
    if n == 0:
        print('  × 没读到帧', flush=True)
        return None
    print(f'  抽帧 {n} 张 ({W}x{H}), 推理 {time.time()-t0:.0f}s', flush=True)

    # 时域平滑（3 帧加权），只在模型分辨率上做
    if n >= 3:
        sm = [mask_list[0]]
        for i in range(1, n - 1):
            sm.append(0.25 * mask_list[i - 1] + 0.5 * mask_list[i] + 0.25 * mask_list[i + 1])
        sm.append(mask_list[-1])
        mask_list = sm

    # 低分辨率 bbox → 换算到全分辨率，取全局并集
    mh, mw = mask_list[0].shape
    sx, sy = W / mw, H / mh
    bboxes = []
    for m in mask_list:
        bb = get_bbox(m)
        if bb:
            bboxes.append((bb[0] * sx, bb[1] * sy, bb[2] * sx, bb[3] * sy))
    if not bboxes:
        print('  × 全程没抠到人', flush=True)
        return None
    x0 = int(min(b[0] for b in bboxes)); y0 = int(min(b[1] for b in bboxes))
    x1 = int(max(b[2] for b in bboxes)); y1 = int(max(b[3] for b in bboxes))
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    half = int(max(x1 - x0, y1 - y0) // 2 * margin)
    print(f'  全局 bbox=({x0},{y0})-({x1},{y1}) half={half}', flush=True)

    # ---- 第二遍：重解码，逐帧精修 alpha + defringe + 裁切 + 缩放 ----
    # 同样不是"内存恒定"：pil_frames 把全部成品帧（384² RGBA ≈ 0.59MB/帧）攒在内存里，
    # 最后一次性交给 PIL 存动图。帧数由上面的 max_frames 兜住。
    pil_frames = []
    container = av.open(in_path)
    try:
        next_t, k = 0.0, 0
        for frame in container.decode(video=0):
            t = float(frame.pts * tb) if frame.pts is not None else k / target_fps
            if t < next_t - 1e-6:
                continue
            if k < n:
                m = mask_list[k]
                full = np.asarray(
                    Image.fromarray((m * 255).astype(np.uint8)).resize((W, H), Image.BILINEAR),
                    dtype=np.float32) / 255.0
                a = refine_alpha(full, alpha_lo, alpha_hi, feather)
                a = shrink_alpha(a, shrink)
                rgb = frame.to_image().convert('RGB')
                xs, ys = max(cx - half, 0), max(cy - half, 0)
                xe, ye = min(cx + half, W), min(cy + half, H)
                cw, ch = xe - xs, ye - ys
                rgba = np.zeros((half * 2, half * 2, 4), dtype=np.uint8)
                # margin<1 或 bbox 贴近边缘时，实际裁切区可能超出画布：clamp 防负索引
                # （numpy 负索引会从尾部取，静默错乱），超出的部分由切片自动截断
                px = max(0, (half * 2 - cw) // 2)
                py = max(0, (half * 2 - ch) // 2)
                rgba[py:py + ch, px:px + cw, :3] = np.asarray(rgb, dtype=np.uint8)[ys:ye, xs:xe]
                rgba[py:py + ch, px:px + cw, 3] = (a[ys:ye, xs:xe] * 255).astype(np.uint8)
                # v3：边缘去色边（在全分辨率下做，细节最全），再做 LANCZOS 抗锯齿缩放
                if defringe > 0:
                    rgba = defringe_rgba(rgba, strength=min(1.0, 0.5 + 0.15 * defringe))
                pil_frames.append(Image.fromarray(rgba, 'RGBA').resize(
                    (target_size, target_size), Image.LANCZOS))
            k += 1
            while next_t <= t + 1e-6:
                next_t += interval
    finally:
        container.close()
    if not pil_frames:
        print('  × 第二遍没产出帧', flush=True)
        return None

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f'{name}.webp')
    # 帧时长按实际抽样间隔计算：源 fps < target 时固定 1000/target_fps 会让动画加速播放
    if len(kept_ts) > 1:
        duration = max(20, int(round(1000 * (kept_ts[-1] - kept_ts[0]) / (len(kept_ts) - 1))))
        if abs(duration - 1000 / target_fps) > 20:
            print(f'  ! 实际帧间隔 {duration}ms 与目标 {int(1000/target_fps)}ms 不符'
                  f'（源帧率低于 --fps？），按实际间隔写入', flush=True)
    else:
        duration = int(1000 / target_fps)
    t0 = time.time()
    # 先写临时文件再 os.replace：直接覆盖 pages/pet/<state>.webp 时，Ctrl-C /
    # 磁盘忙（云盘同步会占着文件）会留下半截 webp，而云盘会把这个坏文件同步到
    # 其它机器；server.py 又以 `max-age=604800, immutable` 发 .webp，客户端会一直缓存坏的。
    tmp_path = f'{out_path}.{os.getpid()}.tmp'
    try:
        pil_frames[0].save(
            tmp_path, 'WEBP', save_all=True, append_images=pil_frames[1:],
            duration=duration, loop=0, disposal=2, lossless=False,
            quality=quality, method=4,   # method=6 编码极慢（324s vs 2s），体积几乎无差
        )
        os.replace(tmp_path, out_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    sz = os.path.getsize(out_path) // 1024
    print(f'  ✓ {len(pil_frames)} 帧, {duration}ms/帧, {sz}KB, 编码 {time.time()-t0:.0f}s → {out_path}', flush=True)
    return out_path


def main():
    ap = argparse.ArgumentParser(description='桌宠视频 → 透明循环 WebP（v3：画质+抠图优化）')
    ap.add_argument('input', nargs='?', help='输入 mp4 路径（单文件）')
    ap.add_argument('--name', help='状态名（如 idle/happy）')
    ap.add_argument('--all', action='store_true', help='批量处理 in-dir/大帅*.mp4')
    ap.add_argument('--in-dir', default=IN_DIR)
    ap.add_argument('--out-dir', default=OUT_DIR)
    ap.add_argument('--model', choices=sorted(MODELS), default='u2net',
                    help='抠像模型（默认 u2net；isnet 对亮肤色易误判，慎用）')
    ap.add_argument('--fps', type=int, default=10)
    ap.add_argument('--size', type=int, default=384, help='输出边长 px（画质档：384 默认，512 高清）')
    ap.add_argument('--margin', type=float, default=1.05)
    ap.add_argument('--alpha-lo', type=float, default=0.40, help='alpha 曲线下限（去雾边）')
    ap.add_argument('--alpha-hi', type=float, default=0.90, help='alpha 曲线上限')
    ap.add_argument('--feather', type=float, default=0.9, help='边缘羽化半径(px)')
    ap.add_argument('--defringe', type=int, default=2,
                    help='边缘去色边强度 0..4（0=关闭；越大白/绿边消化越彻底，过大会糊发丝）')
    ap.add_argument('--shrink', type=int, default=1, help='alpha 蚀刻 px（0=关闭；收最外层半透明杂边）')
    ap.add_argument('--quality', type=int, default=82, help='WebP 质量')
    ap.add_argument('--max-frames', type=int, default=600,
                    help='抽帧上限（防长素材把内存吃爆；超出部分不再抽取）')
    args = ap.parse_args()

    # 参数范围校验：--fps 0 / --size 0 会除零或崩溃，--margin<1 裁切会越界。
    # 必须先过 math.isfinite：`nan <= 0` 与 `nan < 1.0` 都是 False，
    # `--margin nan` 能穿过原来的范围判断，直到渲染循环里 int(x*nan) 才炸
    # （ONNX 第一遍已经跑完，白等几分钟）；`--feather nan` 更糟——
    # `if feather > 0` 为 False，羽化被静默跳过，产出一张参数不对的素材还报告成功。
    def _num(v, lo, hi, label):
        if not math.isfinite(v) or v < lo or v > hi:
            print(f'{label} 必须是 {lo}..{hi} 之间的有限数值，收到 {v!r}'); sys.exit(1)
    _num(args.fps, 1, 30, '--fps')
    _num(args.size, 16, 1024, '--size')
    _num(args.margin, 1.0, 4.0, '--margin')
    _num(args.feather, 0.0, 32.0, '--feather')
    _num(args.quality, 1, 100, '--quality')
    _num(args.defringe, 0, 4, '--defringe')
    _num(args.shrink, 0, 32, '--shrink')
    _num(args.alpha_lo, 0.0, 1.0, '--alpha-lo')
    _num(args.alpha_hi, 0.0, 1.0, '--alpha-hi')
    _num(args.max_frames, 1, 20000, '--max-frames')
    if args.alpha_lo >= args.alpha_hi:
        print('--alpha-lo 必须小于 --alpha-hi（否则 alpha 曲线归零，会报"全程没抠到人"）')
        sys.exit(1)

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

    # 状态名碰撞预警：同义词文件名（如 大帅悲伤/大帅难过）映射到同一状态会互相覆盖输出
    seen_names = {}
    for p, name in tasks:
        if name in seen_names:
            print(f'  ! 状态名冲突: {os.path.basename(seen_names[name])} 与 {os.path.basename(p)}'
                  f' 都映射到 "{name}"，后者会覆盖前者的 webp 输出', flush=True)
        seen_names[name] = p

    session = MattingSession(args.model)
    print(f'计划处理 {len(tasks)} 段：{[n for _, n in tasks]}', flush=True)
    results = []
    for p, name in tasks:
        r = process_one(p, name, args.out_dir, session,
                        target_size=args.size, target_fps=args.fps, margin=args.margin,
                        alpha_lo=args.alpha_lo, alpha_hi=args.alpha_hi,
                        feather=args.feather, defringe=args.defringe,
                        shrink=args.shrink, quality=args.quality,
                        max_frames=args.max_frames)
        if r:
            results.append(r)
    print(f'\n=== 完成 {len(results)}/{len(tasks)} ===', flush=True)
    for r in results:
        print('  ', r)


if __name__ == '__main__':
    main()
