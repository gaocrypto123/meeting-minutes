#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""追加一条节点记录（每个 Phase 结束调用一次）。

节点记录是追加式的审计线索：谁在什么阶段产出了什么、依据是什么、遗留什么。
不要手写时间戳，本脚本取本机当前时间。

用法：
    python append_checkpoint.py "<工作目录>" --id CP2 --phase "Phase 2 切段提炼" \
        --output "02-要点.md（6 段 / 38 条要点）" --basis "01-转写稿.md" \
        --status 完成 --pending "2 条待确认：人名1、上线日期1" --next "CP3 出稿"

    python append_checkpoint.py "<工作目录>" --list      # 列出已有节点
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

ENTRY_TEMPLATE = """## {cid} · {phase}

- 时间：{now}
- 阶段：{phase}
- 产出：{output}
- 依据：{basis}
- 状态：{status}
- 遗留：{pending}
- 下一节点：{next}
{note}
"""


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="追加会议纪要节点记录")
    ap.add_argument("workspace", help="会议工作目录（含 checkpoints.md）")
    ap.add_argument("--id", dest="cid", help="节点编号，如 CP0 / CP1 ...")
    ap.add_argument("--phase", default="", help="阶段名，如 Phase 2 切段提炼")
    ap.add_argument("--output", default="", help="本阶段产出物（文件名 + 规模）")
    ap.add_argument("--basis", default="", help="依据的源文件（可回溯）")
    ap.add_argument("--status", default="完成", help="状态：完成 / 进行中 / 阻塞 / 跳过")
    ap.add_argument("--pending", default="无", help="遗留的待确认项")
    ap.add_argument("--next", default="—", help="下一节点编号")
    ap.add_argument("--note", default="", help="自由补充说明（可选）")
    ap.add_argument("--list", action="store_true", help="只列出已有节点，不追加")
    args = ap.parse_args()

    workdir = Path(args.workspace).expanduser().resolve()
    cp_path = workdir / "过程" / "checkpoints.md"     # v2 结构
    if not cp_path.exists():
        cp_path = workdir / "checkpoints.md"          # 兼容旧结构
    if not cp_path.exists():
        print(f"[错误] 在 {workdir} 下找不到 checkpoints.md（过程/ 或根目录）。"
              f"先跑 init_meeting_workspace.py。", file=sys.stderr)
        return 2

    text = cp_path.read_text(encoding="utf-8")

    if args.list:
        ids = re.findall(r"^##\s+(\S+)\s+·\s+(.+)$", text, flags=re.M)
        if not ids:
            print("（暂无节点记录）")
        for cid, phase in ids:
            print(f"{cid}\t{phase}")
        return 0

    if not args.cid or not args.phase:
        print("[错误] 追加模式必须提供 --id 与 --phase。", file=sys.stderr)
        return 2

    if re.search(rf"^##\s+{re.escape(args.cid)}\b", text, flags=re.M):
        print(f"[警告] 节点 {args.cid} 已存在，仍将追加（节点记录不做原地修改）。", file=sys.stderr)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    note = f"- 备注：{args.note}\n" if args.note else ""
    entry = ENTRY_TEMPLATE.format(
        cid=args.cid,
        phase=args.phase,
        now=now,
        output=args.output or "—",
        basis=args.basis or "—",
        status=args.status,
        pending=args.pending,
        next=args.next,
        note=note,
    )

    with cp_path.open("a", encoding="utf-8") as fh:
        fh.write("\n" + entry)

    print(f"[已追加] {args.cid} · {args.phase} @ {now}")
    print(f"文件：{cp_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
