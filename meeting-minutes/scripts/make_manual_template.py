#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meeting-minutes · Phase 1 路 3：人工补录模板

本机转不了写时（doctor 判定路径 C），给用户一份「边听边填」的结构化模板，
让他自己把会议内容补出来。填完就是一份合格的 01-转写稿.md，直接从 Phase 2 继续。

有 av 依赖且给了音频 → 自动按时长切段，带时间轴；
没有依赖或没给音频 → 生成按议题填写的通用模板。

用法：
    python scripts/make_manual_template.py --out "<工作目录>/01-转写稿.md"
    python scripts/make_manual_template.py "录音.m4a" --out 01-转写稿.md
    python scripts/make_manual_template.py "录音.m4a" --out 01.md --segment 180 --title "结算评审会"
"""

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def hms(sec):
    sec = int(max(0, sec))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def probe_duration(path):
    """尝试读出音频时长；读不到返回 None（不报错、不要求装依赖）。"""
    try:
        import av  # noqa
    except Exception:
        return None, "未安装 av 库，跳过自动分段"
    try:
        container = av.open(str(path))
        stream = container.streams.audio[0]
        dur = float(stream.duration * stream.time_base)
        container.close()
        if dur and dur > 0:
            return dur, None
        return None, "音频时长读不出来"
    except Exception as e:
        return None, f"读取音频失败：{type(e).__name__}"


def build_with_timeline(dur, segment, title):
    n = max(1, int(dur // segment) + (1 if dur % segment else 0))
    lines = [
        f"# 01 · 转写稿（人工补录）",
        "",
        f"- 会议：{title}",
        f"- 音频时长：{hms(dur)}（共 {n} 段，每段 {segment // 60} 分钟）",
        "- 来源：**人工补录**（本机不具备自动转写条件）",
        "- 说明：时间戳为自动切分，仅作定位用；内容请按你听到的填",
        "",
        "> 填写指引",
        "> 1. 每段填「这段在聊什么」+「谁说了什么关键的话」，一句话也行，别追求逐字。",
        "> 2. 听不清的人名 / 数字 / 日期一律写 `[?]`，**不要猜**——猜错了纪要就变成了假证据。",
        "> 3. 整段听不清就写「听不清」，那儿会被当成信息缺口，不会混进正文。",
        "",
        "---",
        "",
    ]
    for i in range(n):
        s0 = i * segment
        s1 = min(dur, (i + 1) * segment)
        lines += [
            f"### 段 {i + 1} · {hms(s0)} - {hms(s1)}",
            "",
            "- 议题：",
            "- 发言要点：",
            "- 结论 / 决定：",
            "- 待确认：",
            "",
        ]
    return lines


def build_generic(title):
    return [
        "# 01 · 转写稿（人工补录）",
        "",
        f"- 会议：{title}",
        "- 来源：**人工补录**（本机不具备自动转写条件）",
        "",
        "> 填写指引",
        "> 1. 按议题分段，一段一填；不必按时间均分。",
        "> 2. 每段写「这段在聊什么」+「谁说了什么关键的话」，一句话也行，别追求逐字。",
        "> 3. 听不清的人名 / 数字 / 日期一律写 `[?]`，**不要猜**。",
        "",
        "---",
        "",
        "### 议题 1",
        "",
        "- 议题：",
        "- 发言要点：",
        "- 结论 / 决定：",
        "- 待确认：",
        "",
        "### 议题 2",
        "",
        "- 议题：",
        "- 发言要点：",
        "- 结论 / 决定：",
        "- 待确认：",
        "",
        "### 议题 3（不够就接着加，删掉多余的）",
        "",
        "- 议题：",
        "- 发言要点：",
        "- 结论 / 决定：",
        "- 待确认：",
        "",
    ]


def main():
    ap = argparse.ArgumentParser(description="meeting-minutes 人工补录模板生成")
    ap.add_argument("audio", nargs="?", default=None, help="音频路径（可选，用于自动切段时间轴）")
    ap.add_argument("--out", required=True, help="输出 Markdown 路径")
    ap.add_argument("--segment", type=int, default=180, help="每段秒数，默认 180")
    ap.add_argument("--title", default="（未填写）", help="会议名")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    dur = None
    note = None
    if args.audio:
        src = Path(args.audio)
        if src.exists():
            dur, note = probe_duration(src)
        else:
            note = f"找不到音频：{src}"

    if dur:
        lines = build_with_timeline(dur, args.segment, args.title)
        print(f"[完成] {out}  按时长切 {max(1, int(dur // args.segment) + 1)} 段，带时间轴")
    else:
        lines = build_generic(args.title)
        tail = f"（{note}）" if note else ""
        print(f"[完成] {out}  通用议题模板{tail}")

    out.write_text("\n".join(lines), encoding="utf-8")
    print("[提示] 把模板交给用户填写；填完直接当作 01-转写稿.md，从 Phase 2 继续。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
