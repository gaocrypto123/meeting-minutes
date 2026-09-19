#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meeting-minutes v2 · 出处锚点校验（Phase 4）

纪要里每条结论都写了出处，例如 `[00:07:42]`。这个脚本把它们**逐个拿去和转写稿
的 JSON 对账**，确认这个时间点真的有对应的发言，而不是编出来的。

这是"可复核"从口号变成事实的关键一步：写错、写偏、编造，都会被机器抓出来。

用法：
    python verify_refs.py "<工作目录>"
    python verify_refs.py --minutes "<...>/纪要/会议纪要.md" --transcript "<...>/原文/转写稿.json"

退出码：0=全部有效；1=存在无效锚点；2=参数/文件错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TS_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]")
ENTRY_RE = re.compile(r"^-\s*\*\*(?P<kind>[\u4e00-\u9fff]{2,6})\s*(?P<id>[A-Za-z]{0,3}\d{0,4})\*\*")
FIELD_RE = re.compile(r"^\s+-\s*(?P<key>[\u4e00-\u9fff]{2,6})\s*[：:]\s*(?P<val>.*)$")
PENDING = ("待确认", "未找到出处", "未定", "不详", "[?]")


def tss_to_seconds(m) -> int:
    h, mnt, sec = int(m.group(1)), int(m.group(2)), m.group(3)
    if sec is None:                      # [12:30] 视作 分:秒
        return h * 60 + mnt
    return h * 3600 + mnt * 60 + int(sec)


def collect_entries(minutes_text: str) -> list:
    """解析纪要条目：类型、编号、行号、字段。"""
    entries, cur = [], None
    for lineno, raw in enumerate(minutes_text.splitlines(), start=1):
        line = raw.rstrip()
        m = ENTRY_RE.match(line.strip())
        if m:
            cur = {"kind": m.group("kind"), "id": (m.group("id") or "").strip(),
                   "line": lineno, "fields": {}}
            entries.append(cur)
            continue
        fm = FIELD_RE.match(line)
        if fm and cur is not None:
            cur["fields"][fm.group("key")] = fm.group("val").strip()
    return entries


def is_pending(v: str) -> bool:
    return (not v) or any(w in v for w in PENDING)


def load_transcript(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    segs = data.get("segments", [])
    turns = data.get("turns", [])
    return {
        "segments": [(float(s["start"]), float(s["end"])) for s in segs],
        "turns": [(float(t["start"]), float(t["end"])) for t in turns],
        "duration": float(data.get("meta", {}).get("duration", 0.0)),
    }


def anchor_hits(t: float, intervals: list, tol: float = 2.0) -> bool:
    """时间点 t 是否落在某段发言里（或落在其起点附近）。"""
    for s, e in intervals:
        if s - tol <= t <= e + tol:
            return True
    return False


def verify(minutes_path: Path, transcript_path: Path) -> tuple:
    minutes = minutes_path.read_text(encoding="utf-8")
    tr = load_transcript(transcript_path)
    intervals = tr["turns"] or tr["segments"]
    entries = collect_entries(minutes)

    results = []
    total = ok = 0
    for e in entries:
        src = e["fields"].get("出处", "")
        anchors = [tss_to_seconds(m) for m in TS_RE.finditer(src)]
        need = not is_pending(e["fields"].get("状态", ""))
        item = {"entry": e, "anchors": anchors, "need_source": need,
                "bad": [], "missing_source": False}
        if need and not anchors:
            if is_pending(src):
                item["missing_source"] = True
            else:
                item["bad"].append("出处里没有可识别的时间戳")
        for t in anchors:
            total += 1
            if anchor_hits(t, intervals):
                ok += 1
            else:
                item["bad"].append(f"[{t // 3600:02d}:{(t % 3600) // 60:02d}:"
                                   f"{t % 60:02d}] 在转写稿里找不到对应发言")
        results.append(item)

    bad_items = [r for r in results if r["bad"] or r["missing_source"]]
    summary = {
        "entries": len(entries), "anchors": total, "anchors_ok": ok,
        "bad_entries": len(bad_items),
        "no_anchor_entries": sum(1 for r in results if not r["anchors"]),
        "duration": tr["duration"],
    }
    return results, summary


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="出处锚点校验")
    ap.add_argument("workdir", nargs="?", default="", help="会议工作目录")
    ap.add_argument("--minutes", default="")
    ap.add_argument("--transcript", default="")
    ap.add_argument("--out", default="", help="报告输出路径")
    args = ap.parse_args()

    if args.workdir:
        wd = Path(args.workdir).expanduser().resolve()
        minutes = Path(args.minutes) if args.minutes else wd / "纪要" / "会议纪要.md"
        transcript = (Path(args.transcript) if args.transcript
                      else wd / "原文" / "转写稿.json")
    else:
        minutes, transcript = Path(args.minutes), Path(args.transcript)

    for p in (minutes, transcript):
        if not p or not p.exists():
            print(f"[错误] 找不到文件：{p}\n"
                  f"       （转写稿 JSON 由 transcribe.py 的 --out-json 生成）", file=sys.stderr)
            return 2

    results, s = verify(minutes, transcript)
    lines = [
        f"# 出处锚点校验报告 · {minutes.parent.parent.name}",
        "",
        f"- 检查对象：`{minutes.name}`",
        f"- 对账依据：`{transcript.name}`（{len(results)} 条要点 / "
        f"音频 {s['duration']:.0f} 秒）",
        f"- 锚点总数：{s['anchors']}，其中**有效 {s['anchors_ok']} 个**、"
        f"无效 {s['anchors'] - s['anchors_ok']} 个",
        f"- 结论：**{'PASS' if not s['bad_entries'] else 'FAIL'}**"
        f"（{s['entries']} 条要点，{s['bad_entries']} 条有问题）",
        "",
    ]
    bad = [r for r in results if r["bad"] or r["missing_source"]]
    if bad:
        lines += ["## 问题明细", "", "| 要点 | 行号 | 问题 |", "|---|---|---|"]
        for r in bad:
            e = r["entry"]
            name = f"{e['kind']} {e['id']}".strip()
            msg = "；".join(r["bad"]) or "需要出处但写的是「待确认」"
            lines.append(f"| {name} | 第 {e['line']} 行 | {msg} |")
        lines += ["", "> 修复只允许两种动作：标注「待确认」，或删除该条目。"
                      "**禁止编造时间戳**——那正是本报告要防的事。", ""]
    else:
        lines += ["", "所有锚点都能在转写稿里找到对应发言。", ""]

    report = "\n".join(lines)
    out = Path(args.out) if args.out else (minutes.parent / "出处校验报告.md")
    out.write_text(report, encoding="utf-8")

    print(f"锚点 {s['anchors_ok']}/{s['anchors']} 有效；"
          f"{s['bad_entries']} 条要点有问题；报告：{out}")
    return 1 if s["bad_entries"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
