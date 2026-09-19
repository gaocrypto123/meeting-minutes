#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初始化会议纪要工作目录（Phase 0 用）。

创建：<输出根目录>/<会议名>_纪要/{meta.md, checkpoints.md}

用法：
    python init_meeting_workspace.py "结算流程改版评审会" \
        --dir "." --type 决策评审 --date 2026-09-18 \
        --location "3F 会议室A" --attendees "张三(主持),李四" --output both
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

TEMPLATES = {
    "跨部门对接": ["会议背景", "议题与讨论要点", "意见总结", "行动项", "待确认与风险"],
    "内部工作会": ["会议背景", "进展同步", "问题与阻塞", "意见总结", "行动项与下周计划"],
    "决策评审": ["会议背景与评审对象", "评审意见", "决策结论", "行动项", "待确认与风险"],
    "外部沟通": ["会议背景", "客户需求与反馈", "我方回应与承诺", "行动项", "待确认与风险"],
}

ALIASES = {
    "跨部门": "跨部门对接", "对接": "跨部门对接", "跨部门对接会": "跨部门对接",
    "内部": "内部工作会", "工作会": "内部工作会", "例会": "内部工作会", "周会": "内部工作会",
    "决策": "决策评审", "评审": "决策评审", "决策评审会": "决策评审",
    "外部": "外部沟通", "客户": "外部沟通", "外部沟通会": "外部沟通",
}

OUTPUT_LABEL = {"word": "Word 纪要", "ppt": "PPT 汇报", "both": "Word 纪要 + PPT 汇报"}

META_TEMPLATE = """# 会议元信息

| 字段 | 值 |
|---|---|
| 会议名称 | {name} |
| 会议类型 | {mtype} |
| 会议日期 | {date} |
| 会议地点 | {location} |
| 参会人 | {attendees} |
| 输出形态 | {output} |
| 工作目录 | {workdir} |
| 建档时间 | {now} |

## 本章节骨架（本类型的模板章节，标题不得删）

{chapters}

## 事实清单（Phase 5 事实复核用，请补齐）

- 参会人与角色：
- 会议地点：
- 会议背景（≤3 句）：
- 议题范围：
"""

CHECKPOINTS_HEADER = """# 节点记录 · {name}

> 规则：只追加，不涂改。每个 Phase 结束必须追加一条。
> 追加命令：`python append_checkpoint.py "<工作目录>" --id CP1 --phase "..." --output "..." --basis "..."`

"""


def sanitize(name: str) -> str:
    """把会议名处理成合法目录名。"""
    clean = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" .")
    clean = re.sub(r"_{2,}", "_", clean)
    return clean or "会议"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="初始化会议纪要工作目录")
    ap.add_argument("meeting_name", help="会议名称，如 结算流程改版评审会")
    ap.add_argument("--dir", default=".", help="输出根目录，默认当前目录")
    ap.add_argument("--type", dest="mtype", default="", help="会议类型：跨部门对接/内部工作会/决策评审/外部沟通")
    ap.add_argument("--date", default="", help="会议日期，如 2026-09-18")
    ap.add_argument("--location", default="待确认", help="会议地点")
    ap.add_argument("--attendees", default="待确认", help="参会人，逗号分隔，主持人加 (主持)")
    ap.add_argument("--output", default="both", choices=["word", "ppt", "both"], help="输出形态")
    ap.add_argument("--force", action="store_true", help="已存在时覆盖 meta.md / checkpoints.md")
    args = ap.parse_args()

    mtype = ALIASES.get(args.mtype.strip(), args.mtype.strip())
    if mtype and mtype not in TEMPLATES:
        print(f"[警告] 未识别的会议类型 '{args.mtype}'，meta.md 中留空，请手动确认。", file=sys.stderr)
        mtype = ""
    if not mtype:
        print("[提示] 未指定 --type。章节骨架留空，Phase 3 前必须补上会议类型。", file=sys.stderr)

    root = Path(args.dir).expanduser().resolve()
    folder = sanitize(args.meeting_name)
    if not folder.endswith("纪要"):
        folder = f"{folder}_纪要"
    workdir = root / folder
    workdir.mkdir(parents=True, exist_ok=True)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    chapters = "\n".join(
        f"{i}. {t}" for i, t in enumerate(TEMPLATES.get(mtype, []), start=1)
    ) or "（未确定会议类型，待补）"

    meta_path = workdir / "meta.md"
    cp_path = workdir / "checkpoints.md"

    if meta_path.exists() and not args.force:
        print(f"[跳过] meta.md 已存在：{meta_path}")
    else:
        meta_path.write_text(
            META_TEMPLATE.format(
                name=args.meeting_name,
                mtype=mtype or "待确认",
                date=args.date or "待确认",
                location=args.location,
                attendees=args.attendees,
                output=OUTPUT_LABEL[args.output],
                workdir=str(workdir),
                now=now,
                chapters=chapters,
            ),
            encoding="utf-8",
        )
        print(f"[创建] {meta_path}")

    if cp_path.exists() and not args.force:
        print(f"[跳过] checkpoints.md 已存在：{cp_path}")
    else:
        cp_path.write_text(CHECKPOINTS_HEADER.format(name=args.meeting_name), encoding="utf-8")
        print(f"[创建] {cp_path}")

    print(f"\n工作目录：{workdir}")
    print("下一步：append_checkpoint.py 写 CP0，然后进入 Phase 1 转写。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
