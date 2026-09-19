#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meeting-minutes · Phase 1 本地离线转写

把会议录音（m4a / mp3 / wav / mp4 / amr …）转成带时间戳的 Markdown 转写稿。
全程本地运行，音频不出本机，不需要任何 API Key。

用法：
    python transcribe.py <音频文件> --out <输出.md>
    python transcribe.py 录音.m4a --out 01-转写稿.md --model medium
    python transcribe.py --help

可选参数：
    --model     small|medium（默认 medium。small 快但远场/远距离录音几乎不可用）
    --language  语言代码，默认 zh
    --chunk     分块秒数，默认 120（被中断后可续跑）
    --threads   CPU 线程数，默认 8
    --no-vad    关闭静音过滤（录音极安静时可试）
    --resume    沿用上次的进度文件继续跑
"""

import argparse
import json
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------------------------------------------------------------- 基础工具

LOG_LINES = []


def log(msg=""):
    print(msg)
    LOG_LINES.append(str(msg))


def hms(sec):
    sec = int(max(0, sec))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def default_model_dir():
    base = Path.home() / ".workbuddy" / "binaries" / "models"
    return base


# ---------------------------------------------------------------- 依赖

def ensure_deps(auto_install=True):
    """确保 av / numpy / faster_whisper 可用，缺则自动安装（腾讯云源）。"""
    missing = []
    for mod in ("av", "numpy", "faster_whisper"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if not missing:
        return True
    if not auto_install:
        log(f"[依赖缺失] {', '.join(missing)}")
        log("  请手动安装：")
        log(f"  {sys.executable} -m pip install -i https://mirrors.cloud.tencent.com/pypi/simple "
            "av numpy faster-whisper")
        return False
    pkg = {"av": "av", "numpy": "numpy", "faster_whisper": "faster-whisper"}
    want = [pkg[m] for m in missing]
    log(f"[安装依赖] {' '.join(want)}（腾讯云源）")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install",
         "-i", "https://mirrors.cloud.tencent.com/pypi/simple", *want],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        log(r.stdout[-2000:])
        log(r.stderr[-2000:])
        log("[安装失败] 请手动执行上面的 pip 命令后重跑。")
        log("           若 pip 源也连不上，说明本机不具备本地转写条件 —— 不要再折腾，")
        log("           改用人工供稿路径（会议平台自带纪要 / 手机录音机转文字 / 剪映讯飞），")
        log("           拿到文字稿直接从 Phase 2 开始。")
        return False
    log("[安装完成]")
    return True


# ---------------------------------------------------------------- 模型下载

MODEL_MIN_BYTES = {          # 低于此大小视为下载不完整
    "small": 460 * 1024 ** 2,
    "medium": 1400 * 1024 ** 2,
    "base": 130 * 1024 ** 2,
}
MODEL_FILES = ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]
# 实测（2026-09）：hf-mirror 可达且支持 Range 续传；huggingface.co 国内常超时；
# ModelScope 返回无 Content-Length 且不支持续传，故不作为主源。
# 同一个镜像给两个 repo 备援，防止单 repo 下线。
MIRRORS = [
    "https://hf-mirror.com/Systran/faster-whisper-{size}/resolve/main/{name}",
    "https://hf-mirror.com/guillaumekln/faster-whisper-{size}/resolve/main/{name}",
    "https://huggingface.co/Systran/faster-whisper-{size}/resolve/main/{name}",
]
# 手动下载兜底：脚本下载失败时，把直链交给用户用浏览器/下载工具自己下
MANUAL_URL = "https://hf-mirror.com/Systran/faster-whisper-{size}/resolve/main/{name}"


def print_manual_guide(size, d):
    """下载失败时的兜底方案。必须给出能直接照做的步骤，不能只报一句失败。"""
    base = MANUAL_URL.format(size=size, name="")
    root = base.rsplit("/", 1)[0]
    log("")
    log("=" * 64)
    log("  自动下载失败 —— 以下是手动方案，照做即可")
    log("=" * 64)
    log("  1. 用浏览器或下载工具（迅雷/IDM 等）打开下面 4 个地址，全部下下来：")
    for name in MODEL_FILES:
        log(f"     {MANUAL_URL.format(size=size, name=name)}")
    log("")
    log(f"  2. 把 4 个文件放进这个目录（没有就新建）：")
    log(f"     {d}")
    log("")
    log(f"  3. 重跑时指定该目录：")
    log(f"     python scripts/transcribe.py <音频> --out 01-转写稿.md "
        f"--model {size} --model-dir \"{d}\"")
    log("")
    if size != "base":
        log("  或者换个更小的模型档位试试（下载量小得多，但远场录音识别率会下降）：")
        if size != "small":
            log("     --model small  461 MiB")
        log("     --model base   138 MiB")
    log("")
    log("  如果上面两步都做不了，说明本机不具备转写条件。此时不要再折腾转写，")
    log("  直接改用人工供稿路径（会议平台自带纪要 / 手机录音机转文字 / 剪映讯飞），")
    log("  拿到文字稿后从 Phase 2 开始，转写这步可以整个跳过。")
    log("=" * 64)


def _fetch(url, dst, expect_min=0, resume=True, tries=3):
    """带 Range 断点续传的下载，返回是否成功。

    重试次数刻意压低（3 次）：下载不动时死磕重试只会让用户干等，
    快速失败后走手动方案反而更快。
    """
    part = Path(str(dst) + ".part")
    for attempt in range(1, tries + 1):
        try:
            have = part.stat().st_size if part.exists() else 0
            headers = {"User-Agent": "Mozilla/5.0"}
            if have and resume:
                headers["Range"] = f"bytes={have}-"
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=60)
            code = resp.status
            clen = int(resp.headers.get("Content-Length", 0))
            total = have + clen
            mode = "ab" if (have and code == 206) else "wb"
            if mode == "wb":
                have = 0
                total = clen
            log(f"  {dst.name}：第 {attempt} 次，已有 {have / 2 ** 20:.0f} MiB / 共 {total / 2 ** 20:.0f} MiB")
            t0 = time.time()
            last = t0
            with open(part, mode) as f:
                while True:
                    buf = resp.read(512 * 1024)
                    if not buf:
                        break
                    f.write(buf)
                    have += len(buf)
                    now = time.time()
                    if now - last > 30:
                        last = now
                        log(f"    {have / 2 ** 20:.0f} / {total / 2 ** 20:.0f} MiB "
                            f"({have / 2 ** 20 / max(1, now - t0):.1f} MiB/s)")
            resp.close()
            if expect_min and have < expect_min:
                log(f"  大小不足（{have / 2 ** 20:.0f} < {expect_min / 2 ** 20:.0f} MiB），重试")
                time.sleep(3)
                continue
            part.rename(dst)
            log(f"  完成 {dst.name}  {dst.stat().st_size / 2 ** 20:.1f} MiB  用时 {time.time() - t0:.0f}s")
            return True
        except Exception as e:
            log(f"  异常 {type(e).__name__}: {e}（第 {attempt} 次）")
            time.sleep(4)
    return False


def ensure_model(size, model_dir=None):
    """保证本地有一份可直接加载的模型目录（普通文件，非符号链接）。"""
    d = Path(model_dir) if model_dir else default_model_dir() / f"faster-whisper-{size}"
    d.mkdir(parents=True, exist_ok=True)
    need_min = MODEL_MIN_BYTES.get(size, 0)
    mb = d / "model.bin"
    if mb.exists() and mb.stat().st_size >= need_min:
        log(f"[模型] 已就绪 {d}（{mb.stat().st_size / 2 ** 20:.0f} MiB）")
        return str(d)
    log(f"[模型] 下载 Systran/faster-whisper-{size} → {d}")
    for name in MODEL_FILES:
        target = d / name
        if target.exists() and target.stat().st_size > 0:
            if name != "model.bin" or target.stat().st_size >= need_min:
                continue
        ok = False
        for tpl in MIRRORS:
            url = tpl.format(size=size, name=name)
            log(f"  ← {url.split('/')[2]}")
            if _fetch(url, target, expect_min=need_min if name == "model.bin" else 0):
                ok = True
                break
        if not ok:
            log(f"[模型下载失败] {name} —— 所有镜像源均不可用")
            print_manual_guide(size, d)
            return None
    # config.json 必须能解析，否则加载会炸
    try:
        json.loads((d / "config.json").read_text(encoding="utf-8"))
    except Exception:
        log("[模型损坏] config.json 无法解析，请删除该目录后重跑")
        return None
    return str(d)


# ---------------------------------------------------------------- 解码

def decode_audio(path):
    """解码为 16k 单声道 float32，并归一化到 peak 0.95。"""
    import av  # noqa
    import numpy as np

    container = av.open(str(path))
    stream = container.streams.audio[0]
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    chunks = []
    for frame in container.decode(stream):
        for f in resampler.resample(frame):
            chunks.append(f.to_ndarray().reshape(-1))
    for f in resampler.resample(None):
        chunks.append(f.to_ndarray().reshape(-1))
    container.close()
    a = np.concatenate(chunks).astype("float32") / 32768.0
    rms = float(((a ** 2).mean()) ** 0.5)
    peak = float(abs(a).max())
    if peak > 0:
        a = a * (0.95 / peak)          # 低音量会议录音必须做，识别率提升明显
    log(f"[音频] {len(a) / 16000 / 60:.1f} 分钟  原始 RMS={rms:.4f}  peak={peak:.3f} → 已归一化")
    if rms < 0.01:
        log("[警告] 音量过低（RMS < 0.01），转写准确率会很差。建议换更近的录音。")
    return a


# ---------------------------------------------------------------- 主流程

# 默认不使用 initial_prompt。实测：提示词里的任何词组（哪怕是"讨论工作方案"这种
# 场景描述）都可能被模型当成台词原样吐出，形成几十次重复的幻觉段。需要时再用
# --prompt 自定义，并自行检查输出里有没有泄漏。
DEFAULT_PROMPT = None
# 模型把提示词/版权语当台词输出时的常见伪段，汇总时剔除
LEAK_PATTERNS = ("请输出简体中文", "请输出简体", "字幕由", "以下是", "请识别", "Transcribed by")


def is_hallucination(text, min_repeat=4):
    """检测「同一片段机械重复」的幻觉段，如「我吃了一口我吃了一口…」。"""
    t = "".join(text.split())
    n = len(t)
    if n < 8:
        return False
    max_p = n // min_repeat
    for p in range(2, max_p + 1):
        unit = t[:p]
        k = n // p
        if k < min_repeat:
            break
        # 允许尾部截断：前 k 个周期能覆盖整串即判定为重复
        if t[: k * p] == unit * k:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description="meeting-minutes Phase 1 本地离线转写")
    ap.add_argument("audio", help="音频/视频文件路径")
    ap.add_argument("--out", required=True, help="输出 Markdown 路径，如 01-转写稿.md")
    ap.add_argument("--model", default="medium", choices=["base", "small", "medium"],
                    help="模型档位。base 138MiB(近场)/small 461MiB(折中)/medium 1457MiB(远场推荐)。"
                         "下载不动时先试 base 把流程跑通")
    ap.add_argument("--language", default="zh")
    ap.add_argument("--chunk", type=float, default=120.0, help="分块秒数，默认 120")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--model-dir", default=None, help="自定义模型目录")
    ap.add_argument("--no-vad", action="store_true", help="关闭静音过滤")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT,
                    help="自定义 initial_prompt（默认不给，避免提示词泄漏成台词）")
    ap.add_argument("--keep-repeat", action="store_true",
                    help="保留机械重复的幻觉段（默认自动剔除）")
    ap.add_argument("--limit", type=float, default=0,
                    help="只转写前 N 秒（试跑音质用，不写进度文件）")
    ap.add_argument("--resume", action="store_true", help="沿用上次进度继续跑")
    ap.add_argument("--no-install", action="store_true", help="缺依赖时不自动安装")
    args = ap.parse_args()

    src = Path(args.audio)
    if not src.exists():
        log(f"[错误] 找不到音频：{src}")
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not ensure_deps(auto_install=not args.no_install):
        return 2
    model_dir = ensure_model(args.model, args.model_dir)
    if not model_dir:
        return 2

    audio = decode_audio(src)
    dur = len(audio) / 16000
    if args.limit and args.limit < dur:
        audio = audio[:int(args.limit * 16000)]
        dur = len(audio) / 16000
        log(f"[试跑模式] 只处理前 {args.limit:.0f} 秒")

    from faster_whisper import WhisperModel
    log(f"[加载模型] {args.model} int8 cpu × {args.threads} 线程")
    t0 = time.time()
    model = WhisperModel(model_dir, device="cpu", compute_type="int8", cpu_threads=args.threads)
    log(f"  用时 {time.time() - t0:.0f}s")

    prog_file = out.with_suffix(out.suffix + f".{args.model}.progress.json")
    done = {}
    if args.resume and not args.limit and prog_file.exists():
        try:
            done = json.loads(prog_file.read_text(encoding="utf-8"))
        except Exception:
            done = {}

    n = int(dur // args.chunk) + 1
    log(f"[转写] {n} 块 × {args.chunk:.0f}s，预计 {(dur / 60) * 1.9:.0f}-{int((dur / 60) * 4)} 分钟（CPU）")
    t_all = time.time()
    for i in range(n):
        key = str(i)
        if key in done:
            log(f"  块 {i + 1}/{n} 已有结果，跳过")
            continue
        s0, s1 = i * args.chunk, min(dur, (i + 1) * args.chunk)
        seg = audio[int(s0 * 16000):int(s1 * 16000)]
        if len(seg) < 1600:
            done[key] = []
            if not args.limit:
                prog_file.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
            continue
        t1 = time.time()
        segments, info = model.transcribe(
            seg, language=args.language, beam_size=5,
            vad_filter=not args.no_vad,
            vad_parameters=dict(min_silence_duration_ms=350, speech_pad_ms=200),
            condition_on_previous_text=False,
            temperature=[0.0, 0.2, 0.4],
            initial_prompt=args.prompt,
        )
        rows = []
        for s in segments:
            txt = (s.text or "").strip()
            if txt:
                rows.append([round(s0 + s.start, 2), round(s0 + s.end, 2), txt])
        done[key] = rows
        if not args.limit:
            prog_file.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
        log(f"  块 {i + 1}/{n}（{hms(s0)}-{hms(s1)}）{len(rows)} 段，用时 {time.time() - t1:.0f}s")

    rows = []
    for i in range(n):
        rows += done.get(str(i), [])
    rows.sort(key=lambda r: r[0])

    dedup, leaks, repeats = [], 0, 0
    for r in rows:
        if dedup and dedup[-1][2] == r[2]:
            dedup[-1][1] = r[1]
            continue
        if any(b in r[2] for b in LEAK_PATTERNS):
            leaks += 1
            continue
        if not args.keep_repeat and is_hallucination(r[2]):
            repeats += 1
            continue
        dedup.append(r)

    lines = [
        "# 01 · 转写稿（机器转写，未经人工校对）",
        "",
        f"- 源文件：`{src.name}`",
        f"- 音频时长：{hms(dur)}",
        f"- 转写引擎：faster-whisper **{args.model}**（int8 / CPU / 本地离线，音频不出本机）",
        "- 说明：机器转写存在同音字与断句误差；人名、数字、日期听不清处不做猜测，一律列入待确认清单",
    ]
    if leaks or repeats:
        lines.append(f"- 已自动剔除：提示词泄漏 {leaks} 条、机械重复幻觉段 {repeats} 条"
                     "（这些内容不可信，对应时间段视为信息缺口）")
    lines += ["", "---", ""]
    for i, (st, _ed, txt) in enumerate(dedup, 1):
        lines.append(f"{i}. [{hms(st)}] {txt}")
    lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")

    log(f"[完成] {out}  {len(dedup)} 行，剔除提示词泄漏 {leaks} 条、"
        f"机械重复幻觉 {repeats} 条，总用时 {time.time() - t_all:.0f}s")
    if args.limit:
        log("[提示] 这是试跑结果。确认可用后去掉 --limit 跑全量。")
    else:
        log(f"[提示] 进度文件 {prog_file.name} 已保留；中断后加 --resume 可续跑。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
