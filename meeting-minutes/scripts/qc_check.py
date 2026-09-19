#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会议纪要确定性质检（Phase 4 用）。质检只查不补，不代写内容。

五项检查：
  Q1 可回溯性      条件条目是否都带"出处"（状态=待确认者除外）
  Q2 章节完整性    对照会议类型模板，一级章节标题是否齐全
  Q3 行动项三要素  每条行动项是否有 责任人 / 交付物 / 截止日
  Q4 无凭空内容    正文里的日期与数量能否在转写稿中找到来源
  Q5 去重          同一事项是否在多条/多章重复出现

用法：
    python qc_check.py "<工作目录>/03-纪要初稿.md" \
        --transcript "<工作目录>/01-转写稿.md" \
        --template 决策评审 \
        --out "<工作目录>/04-质检报告.md"

退出码：0=PASS，1=FAIL，2=参数/文件错误。
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import load_templates, resolve_template  # noqa: E402
from verify_refs import (  # noqa: E402
    TS_RE as REF_TS_RE, anchor_hits, is_pending as ref_pending, load_transcript,
    tss_to_seconds,
)

KNOWN_KINDS = {"需求", "共识", "决策", "技术要点", "行动项", "方案", "风险", "问题", "进展", "待定事项"}
SOURCE_REQUIRED = {"需求", "共识", "决策", "技术要点", "行动项", "方案"}
ACTION_FIELDS = ["责任人", "交付物", "截止日"]
FIELD_KEYS = {"状态", "责任人", "交付物", "截止日", "出处", "引用", "依据", "备注", "说明", "影响", "缺失点", "建议确认对象", "范围", "关联"}
PENDING_WORDS = ("待确认", "未找到出处", "未定", "不详", "[?]")

ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)-\s+\*\*(?P<bold>[^*]+?)\*\*\s*[：:]\s*(?P<title>.+?)\s*$")
BOLD_SPLIT_RE = re.compile(r"^(?P<kind>[\u4e00-\u9fff]{2,6})\s*(?P<cid>[A-Za-z]{0,3}\d{0,4})\s*$")
FIELD_RE = re.compile(r"^[ \t]+-\s*(?P<key>[\u4e00-\u9fff]{2,6})\s*[：:]\s*(?P<value>.*?)\s*$")
CHAPTER_RE = re.compile(r"^##\s+(?:第?\s*)?(?P<num>\d+)\s*[.、]?\s*(?P<title>.+?)\s*$")
SUBCHAPTER_RE = re.compile(r"^#{3,}\s")

DATE_PATTERNS = [
    re.compile(r"\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?"),
    re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日"),
]
QTY_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:万|亿|元|%|％|天|人|次|个|小时|分钟|页|条|轮|倍|台|周|工作日|个月|季度|月)"
)
BARE_NUM_RE = re.compile(r"(?<![\d\-.·])\d{4,}(?![\d\-.·])")
# 转写稿的 [00:12:30] 时间戳不参与 Q4 数字比对（否则行号/时间戳会污染数字来源判定）
TS_RE = re.compile(r"\[\s*\d{1,2}\s*:\s*\d{2}(?:\s*:\s*\d{2})?\s*\]")

PUNCT_RE = re.compile(r"[\s，。、；：:,.!！?？\-—（）()【】\[\]「」“”\"'/\\%％]+")


def norm_text(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def is_pending(value: str) -> bool:
    return (not value) or any(w in value for w in PENDING_WORDS)


def norm_title(s: str) -> str:
    return PUNCT_RE.sub("", s or "")


def parse_doc(text: str):
    """返回 (chapters, items, unknown_bold, unknown_fields, unparsed_bullets)"""
    chapters, items, unknown_bold, unknown_fields = [], [], [], set()
    unparsed = []
    current_chapter = None
    current_item = None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()

        m = CHAPTER_RE.match(line)
        if m:
            title = m.group("title").strip()
            chapters.append({"num": int(m.group("num")), "title": title, "line": lineno})
            current_chapter = title
            current_item = None
            continue

        if SUBCHAPTER_RE.match(line):
            current_item = None
            continue

        m = ITEM_RE.match(line)
        if m:
            bold = m.group("bold").strip()
            ms = BOLD_SPLIT_RE.match(bold)
            current_item = None
            if ms and ms.group("kind") in KNOWN_KINDS:
                current_item = {
                    "cid": (ms.group("cid") or "").strip() or f"L{lineno}",
                    "kind": ms.group("kind"),
                    "title": m.group("title").strip(),
                    "fields": {},
                    "chapter": current_chapter,
                    "line": lineno,
                }
                items.append(current_item)
            else:
                unknown_bold.append((lineno, bold))
            continue

        m = FIELD_RE.match(line)
        if m:
            key, value = m.group("key"), m.group("value").strip()
            if key in FIELD_KEYS:
                if current_item is not None:
                    current_item["fields"][key] = value
            else:
                unknown_fields.add(key)
            continue

        if line.strip() and line.lstrip().startswith("-") and current_item is None and ("：" in line or ":" in line):
            unparsed.append((lineno, line.strip()[:60]))

    return chapters, items, unknown_bold, unknown_fields, unparsed


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="会议纪要确定性质检")
    ap.add_argument("minutes", help="纪要初稿路径，如 03-纪要初稿.md")
    ap.add_argument("--transcript", default="", help="转写稿路径，用于 Q4 比对来源")
    ap.add_argument("--transcript-json", default="",
                    help="转写稿 JSON（transcribe.py --out-json 产出），用于 Q6 锚点校验")
    ap.add_argument("--template", default="", help="会议类型：跨部门对接/内部工作会/决策评审/外部沟通")
    ap.add_argument("--out", default="", help="质检报告输出路径，默认与纪要同目录的 04-质检报告.md")
    ap.add_argument("--meeting-name", default="", help="会议名称，写入报告标题")
    args = ap.parse_args()

    minutes_path = Path(args.minutes).expanduser().resolve()
    if not minutes_path.exists():
        print(f"[错误] 找不到纪要文件：{minutes_path}", file=sys.stderr)
        return 2

    doc = minutes_path.read_text(encoding="utf-8")
    chapters, items, unknown_bold, unknown_fields, unparsed = parse_doc(doc)

    if not items:
        print("[警告] 未解析到任何要点条目。检查条目格式是否为 `- **决策 D1**：……`", file=sys.stderr)

    templates = load_templates()
    template_key, expected, q2_note = "", [], ""
    if args.template:
        resolved = resolve_template(args.template)
        if resolved:
            template_key = resolved
            expected = templates[resolved]
        else:
            q2_note = (f"未能识别会议类型 '{args.template}'；"
                       f"可选：{'、'.join(templates)}")
    else:
        template_key = "未指定"
        q2_note = "未提供 --template，本节未执行"

    results = {}

    # ---- Q1 可回溯性 ----
    q1_fail, q1_pending = [], []
    for it in items:
        if it["kind"] not in SOURCE_REQUIRED:
            continue
        status = it["fields"].get("状态", "")
        src = it["fields"].get("出处", "")
        if is_pending(status):
            q1_pending.append(it)
        elif is_pending(src):
            q1_fail.append((it, "缺出处或出处为'未找到出处'"))
    results["Q1"] = (not q1_fail, q1_fail)

    # ---- Q2 章节完整性 ----
    q2_fail, q2_extra = [], []
    if expected:
        got = [norm_title(c["title"]) for c in chapters]
        got_set = set(got)
        for t in expected:
            if norm_title(t) not in got_set:
                q2_fail.append((None, f"模板章节缺失：{t}"))
        normal_extra = {norm_title(t) for t in ["附录", "附录A 决策清单", "附录B 行动项清单", "附录 A", "附录 B"]}
        for c in chapters:
            n = norm_title(c["title"])
            if norm_title(c["title"]) in {norm_title(t) for t in expected}:
                continue
            if n in normal_extra or n.startswith("附录"):
                continue
            q2_extra.append((None, f"非模板章节：{c['title']}（第 {c['line']} 行）"))
        if not chapters:
            q2_fail.append((None, "未解析到任何一级章节（格式应为 `## 1. 标题`）"))
    results["Q2"] = (q2_note == "" and not q2_fail, q2_fail)

    # ---- Q3 行动项三要素 ----
    q3_fail, q3_pending = [], []
    for it in items:
        if it["kind"] != "行动项":
            continue
        missing = [f for f in ACTION_FIELDS if not it["fields"].get(f, "").strip()]
        pending = [f for f in ACTION_FIELDS if is_pending(it["fields"].get(f, ""))]
        if missing:
            q3_fail.append((it, "缺字段：" + "、".join(missing)))
        elif pending:
            q3_pending.append((it, "、".join(pending)))

    # 意见总结/决策章节里若出现未标类型的行动句，属格式缺失，只提示
    results["Q3"] = (not q3_fail, q3_fail)

    # ---- Q4 无凭空内容 ----
    trans_raw = ""
    if args.transcript:
        tp = Path(args.transcript).expanduser().resolve()
        if tp.exists():
            trans_raw = tp.read_text(encoding="utf-8")
        else:
            print(f"[警告] 找不到转写稿：{tp}，Q4 降级为仅清单提示。", file=sys.stderr)
    trans_clean = TS_RE.sub(" ", trans_raw)
    trans_norm = norm_text(trans_clean)
    trans_digits = re.sub(r"\D", " ", trans_norm)

    def collect_tokens(text: str):
        found = []
        for pat in DATE_PATTERNS:
            found += [m.group(0) for m in pat.finditer(text)]
        found += [m.group(0) for m in QTY_RE.finditer(text)]
        found += [m.group(0) for m in BARE_NUM_RE.finditer(text)]
        out, seen_tok = [], set()
        for t in found:
            n = norm_text(t)
            if n and n not in seen_tok:
                seen_tok.add(n)
                out.append(n)
        return out

    def date_variants(tok: str):
        """把 2026-10-15 这类写法拆成 10月15日 / 10/15 等变体，用于宽松比对。"""
        nums = re.findall(r"\d+", tok)
        if not nums:
            return []
        if DATE_PATTERNS[0].fullmatch(tok) and len(nums) >= 3:
            mo, da = nums[1], nums[2]
        elif DATE_PATTERNS[1].fullmatch(tok) and len(nums) >= 2:
            mo, da = nums[0], nums[1]
        else:
            return []
        mo = mo.lstrip("0") or "0"
        da = da.lstrip("0") or "0"
        return [f"{mo}月{da}日", f"{mo}月{da}号", f"{mo}/{da}", f"{mo}-{da}", f"{mo}.{da}"]

    q4_fail, q4_warn = [], []
    if trans_norm:
        for it in items:
            pool = [it["title"]] + [
                v for k, v in it["fields"].items() if k not in ("出处", "引用") and not is_pending(v)
            ]
            for text in pool:
                for tok in collect_tokens(text):
                    if tok in trans_norm:
                        continue
                    variants = date_variants(tok)
                    if variants and any(v in trans_norm for v in variants):
                        q4_warn.append((it, f"{tok}（原文以其他写法出现，请人工确认）"))
                        continue
                    digits = re.sub(r"\D", "", tok)
                    if digits and re.search(rf"(?<!\d){re.escape(digits)}(?!\d)", trans_digits):
                        q4_warn.append((it, f"{tok}（数字在原文出现，组合形式不同，请人工确认）"))
                        continue
                    hard = bool(variants) or bool(QTY_RE.fullmatch(tok))
                    (q4_fail if hard else q4_warn).append((it, f"{tok}（原文中查无来源）"))
    else:
        q4_warn.append((None, "未提供 --transcript，Q4 未实际比对"))
    # 去重 token
    seen = set()
    q4_fail = [x for x in q4_fail if not (x[1] in seen or seen.add(x[1]))]
    seen = set()
    q4_warn = [x for x in q4_warn if not (x[1] in seen or seen.add(x[1]))]
    results["Q4"] = (not q4_fail, q4_fail)

    # ---- Q5 去重 ----
    q5_fail, q5_warn = [], []
    bucket = {}
    for it in items:
        key = (it["kind"], norm_title(it["title"])[:14])
        if len(key[1]) >= 6:
            bucket.setdefault(key, []).append(it)
    for (kind, _), group in bucket.items():
        if len(group) > 1:
            ids = "、".join(f"{g['kind']} {g['cid']}(L{g['line']})" for g in group)
            q5_fail.append((group[0], f"同类重复条目：{ids}"))

    cross = {}
    for it in items:
        n = norm_title(it["title"])
        if len(n) >= 10:
            cross.setdefault(n, []).append(it)
    for n, group in cross.items():
        kinds = {g["kind"] for g in group}
        if len(kinds) > 1:
            q5_warn.append((group[0], f"同一表述出现在不同类型：{'、'.join(g['kind'] for g in group)}"))
    results["Q5"] = (not q5_fail, q5_fail)

    # ---- Q6 出处可验证（锚点对账） ----
    q6_fail, q6_note = [], ""
    if args.transcript_json:
        tj = Path(args.transcript_json).expanduser().resolve()
        if tj.exists():
            tr = load_transcript(tj)
            intervals = tr["turns"] or tr["segments"]
            for it in items:
                src = it["fields"].get("出处", "")
                anchors = [tss_to_seconds(m) for m in REF_TS_RE.finditer(src)]
                if not ref_pending(it["fields"].get("状态", "")) and not anchors:
                    q6_fail.append((it, "需要出处，但出处里没有可识别的时间戳"))
                    continue
                for t in anchors:
                    if not anchor_hits(t, intervals):
                        q6_fail.append((it, f"时间戳 "
                                            f"{t // 3600:02d}:{(t % 3600) // 60:02d}:{t % 60:02d}"
                                            f" 在转写稿里找不到对应发言"))
        else:
            q6_note = f"找不到转写稿 JSON：{tj}"
    else:
        q6_note = "未提供 --transcript-json"
    results["Q6"] = (not q6_fail, q6_fail)

    # ---- 报告 ----
    label = {"Q1": "可回溯性", "Q2": "章节完整性", "Q3": "行动项三要素", "Q4": "无凭空内容", "Q5": "去重"}
    label["Q6"] = "出处可验证"
    order = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]
    notes = {"Q2": q2_note, "Q6": q6_note}
    executed = [k for k in order if not notes.get(k)]
    passed = sum(1 for k in executed if results[k][0])
    total = len(executed)
    if passed < total:
        overall = "FAIL"
    elif total < len(order):
        overall = "INCOMPLETE"
    else:
        overall = "PASS"

    meeting_name = args.meeting_name or minutes_path.parent.name.replace("_纪要", "") or "未命名会议"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# 质检报告 · {meeting_name}",
        "",
        f"- 检查对象：`{minutes_path.name}`",
        f"- 对照模板：{template_key or '未指定'}",
        f"- 结论：**{overall}**（{passed} 项通过 / {total - passed} 项未通过）",
        f"- 生成时间：{now}",
        f"- 解析到：{len(chapters)} 个一级章节 / {len(items)} 条要点",
        "",
        "| 检查项 | 结果 | 问题条目 | 修复动作 |",
        "|---|---|---|---|",
    ]
    for k in order:
        ok, fails = results[k]
        if notes.get(k):
            lines.append(f"| Q{k[1]} {label[k]} | 未执行 | {notes[k]} | 补上参数后重跑 |")
            continue
        if ok:
            lines.append(f"| Q{k[1]} {label[k]} | PASS | — | — |")
        else:
            detail = "；".join(f"{it['kind']} {it['cid']}(L{it['line']}) {msg}" if it else msg for it, msg in fails)[:300]
            lines.append(f"| Q{k[1]} {label[k]} | FAIL | {detail} | 回 Phase 3：标注「待确认」或删除 |")

    lines += ["", "## 失败明细", ""]
    any_fail = False
    for k in order:
        ok, fails = results[k]
        if ok or notes.get(k):
            continue
        any_fail = True
        lines.append(f"### {k} {label[k]}")
        for it, msg in fails:
            loc = f"第 {it['line']} 行 · {it['kind']} {it['cid']}｜{it['title'][:40]}" if it else "（文档级）"
            lines.append(f"- {loc} → {msg}")
        lines.append("")
    if not any_fail:
        lines.append("（无失败项）")
        lines.append("")

    lines += ["## 提示与需人工补判项", ""]
    for title, warns in [
        ("Q4 弱证据（数字在原文出现但形式不同）", q4_warn),
        ("Q5 疑似跨章节重复", q5_warn),
        ("Q2 非模板章节", q2_extra),
    ]:
        if warns:
            lines.append(f"### {title}")
            for it, msg in warns:
                loc = f"L{it['line']} · {it['kind']} {it['cid']}" if it else "（文档级）"
                lines.append(f"- {loc} → {msg}")
            lines.append("")
    if unknown_bold:
        lines.append("### 未识别的要点类型（格式问题）")
        for lineno, bold in unknown_bold:
            lines.append(f"- 第 {lineno} 行 `**{bold}**` → 类型须取：{'、'.join(sorted(KNOWN_KINDS))}")
        lines.append("")
    if unknown_fields:
        lines.append("### 未识别的字段名")
        lines.append(f"- {'、'.join(sorted(unknown_fields))} → 字段名须取：{'、'.join(sorted(FIELD_KEYS))}")
        lines.append("")
    if unparsed:
        lines.append("### 章节外的散落列表项（可能漏标类型）")
        for lineno, snippet in unparsed[:20]:
            lines.append(f"- 第 {lineno} 行：{snippet}")
        lines.append("")
    lines += [
        "### 必须人工补判的三项（脚本无法判定）",
        "- 章节顺序是否合理，是否符合会议实际推进逻辑",
        "- 结论措辞是否与原文语气一致（有无把讨论强度写高或写低）",
        "- 有无「看起来很对但原文没说过」的结论句",
        "",
        "## 待确认统计",
        "",
        f"- 条件条目中状态=待确认：{len(q1_pending)} 条",
        f"- 行动项字段值为待确认/未定：{len(q3_pending)} 条",
        "",
        "> 修复后请重跑本脚本覆盖本报告，并追加一条 checkpoint（R8：只允许标注「待确认」或删除，禁止补写原文没有的内容）。",
        "",
    ]

    out_path = Path(args.out).expanduser().resolve() if args.out else minutes_path.parent / "04-质检报告.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"结论：{overall}（{passed}/{total} 项通过）")
    for k in order:
        ok, fails = results[k]
        if notes.get(k):
            print(f"  {k} {label[k]}：未执行（{notes[k]}）")
        else:
            print(f"  {k} {label[k]}：{'PASS' if ok else 'FAIL' + '（' + str(len(fails)) + ' 项）'}")
    print(f"报告：{out_path}")
    return 1 if overall == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
