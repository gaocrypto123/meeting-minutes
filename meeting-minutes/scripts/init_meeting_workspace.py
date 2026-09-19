#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meeting-minutes v2 · Phase 0 建档（含开工确认单）

建出三层结构，让「原文」和「纪要」物理分开：

    <根目录>/<会议名>_<日期>/
    ├── 原文/        转写稿（机器产出，不改动）
    ├── 纪要/        交付物（Word / HTML / 待确认清单 / 图文摘要）
    └── 过程/        meta.md（开工确认单）、checkpoints.md（节点记录）

用法：
    python init_meeting_workspace.py "会议纪要技能优化讨论" --dir "D:\\会议" \
        --type 内部工作会 --date 2026-09-19 --speakers 4 \
        --attendees "A(主持,发言),B(发言),C(发言),D(发言),某同学(未发言)" \
        --output word,digest --noise keep
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import load_config, load_templates, resolve_template  # noqa: E402

OUTPUT_LABEL = {
    "word": "Word 纪要", "digest": "图文摘要", "ppt": "PPT（按需生成）",
    "both": "Word 纪要 + 图文摘要", "all": "Word 纪要 + 图文摘要 + PPT",
}

META_TEMPLATE = """# 开工确认单 · {name}

| 项目 | 内容 |
|---|---|
| 会议名称 | {name} |
| 会议类型 | {mtype} |
| 会议日期 | {date} |
| 会议地点 | {location} |
| 参会人 | {attendees} |
| 说话人数 | {speakers} |
| 交付形态 | {output} |
| 无关段落 | {noise} |
| 建档时间 | {now} |
| 工作目录 | `{workdir}` |

## 章节骨架（本类型模板，标题不得删；不涉及写「本次不适用」）

{chapters}

## 事实清单（Phase 5 复核用，逐步补齐）

- 参会人与角色：
- 会议地点：
- 会议背景（≤3 句）：
- 议题范围：
- 无效区间（静音/闲聊/幻觉）：
"""

NOISE_LABEL = {"keep": "保留并标记【非议题】", "drop": "从正文剔除"}


def sanitize(name: str) -> str:
    clean = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" .")
    return re.sub(r"_{2,}", "_", clean) or "会议"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="meeting-minutes v2 建档")
    ap.add_argument("meeting_name")
    ap.add_argument("--dir", default=".", help="输出根目录")
    ap.add_argument("--type", dest="mtype", default="",
                    help="会议类型：内部工作会/决策评审会/跨部门对接会/外部沟通会/采访访谈")
    ap.add_argument("--date", default="", help="会议日期，如 2026-09-19")
    ap.add_argument("--location", default="待确认")
    ap.add_argument("--attendees", default="待确认",
                    help="逗号分隔；未发言的人标注 (未发言)")
    ap.add_argument("--speakers", default="", help="说话人数，如 4；留空则自动判断")
    ap.add_argument("--output", default="word,digest", help="word,digest,ppt，逗号分隔")
    ap.add_argument("--noise", default="keep", choices=["keep", "drop"],
                    help="寒暄/闲聊：keep 保留标记，drop 剔除")
    ap.add_argument("--suffix", default="", help="目录后缀，默认用日期")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    templates = load_templates()
    cfg = load_config()
    mtype = resolve_template(args.mtype)
    if args.mtype.strip() and not mtype:
        print(f"[警告] 模板里没有会议类型「{args.mtype}」。可选："
              f"{'、'.join(templates)}", file=sys.stderr)
    if not mtype:
        print("[提示] 未指定会议类型，Phase 3 前必须补上。", file=sys.stderr)

    suffix = args.suffix or args.date or datetime.now().strftime("%Y-%m-%d")
    folder = sanitize(f"{args.meeting_name}_{suffix}")
    workdir = Path(args.dir).expanduser().resolve() / folder
    for sub in ("原文", "纪要", "过程"):
        (workdir / sub).mkdir(parents=True, exist_ok=True)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    chapters = "\n".join(f"{i}. {t}" for i, t in
                         enumerate(templates.get(mtype, []), start=1)) or "（未定，待补）"
    outs = [OUTPUT_LABEL.get(x.strip(), x.strip())
            for x in args.output.split(",") if x.strip()]
    speakers = args.speakers.strip() or str(cfg["diarization"].get("speakers", 0) or "自动判断")

    meta_path = workdir / "过程" / "meta.md"
    if meta_path.exists() and not args.force:
        print(f"[跳过] meta.md 已存在：{meta_path}")
    else:
        meta_path.write_text(META_TEMPLATE.format(
            name=args.meeting_name, mtype=mtype or "待确认", date=args.date or "待确认",
            location=args.location, attendees=args.attendees, speakers=speakers,
            output=" + ".join(outs) or "待确认", noise=NOISE_LABEL[args.noise],
            now=now, workdir=str(workdir), chapters=chapters), encoding="utf-8")
        print(f"[创建] {meta_path}")

    cp_path = workdir / "过程" / "checkpoints.md"
    if not cp_path.exists() or args.force:
        cp_path.write_text(
            f"# 节点记录 · {args.meeting_name}\n\n"
            "> 规则：只追加，不涂改。每个 Phase 结束追加一条。\n"
            '> 追加命令：`python scripts/append_checkpoint.py "<工作目录>" '
            '--id CP1 --phase "..." --output "..." --basis "..."`\n\n',
            encoding="utf-8")
        print(f"[创建] {cp_path}")

    print(f"\n工作目录：{workdir}")
    print(f"  原文\\   转写稿放这里")
    print(f"  纪要\\   交付物放这里")
    print(f"  过程\\   meta.md / checkpoints.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
