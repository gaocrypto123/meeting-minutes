#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 SKILL.md + references/*.md 合成一份「单文件版」，方便直接粘给 AI 用。

生成的 `单文件版-可直接粘贴给AI.md` 由脚本维护，不要手改——
改了 SKILL.md 或 references 之后重跑本脚本即可。

用法：
    python scripts/make_single_file.py
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
OUT_NAME = "单文件版-可直接粘贴给AI.md"

PARTS = [
    ("第一部分 · 主流程", "SKILL.md"),
    ("第二部分 · 章节模板（含采访访谈）", "references/templates.md"),
    ("第三部分 · 证据字段与质检规范", "references/evidence-and-qc.md"),
    ("第四部分 · 交付物格式规范", "references/output-formats.md"),
]

HEAD = """# meeting-minutes-v2 · 会议纪要技能（单文件合订版）

> **怎么用**：把整份内容复制，粘贴进 AI 对话，然后说
> "按这个技能把下面的录音/文字整理成会议纪要"。
> 纯文字流程不受影响；**转写、说话人分离、质检、出 Word/PPT 需要 scripts/，
> 这些脚本不在本文件里**（本文件只是说明书）。
>
> 本文件由 `scripts/make_single_file.py` 自动生成，生成时间：{now}
> 不要手改，改源头（SKILL.md / references）后重新生成。

---

"""


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].lstrip("\n")
    return text


def main() -> int:
    chunks = [HEAD.format(now=datetime.now().strftime("%Y-%m-%d %H:%M"))]
    missing = []
    for title, rel in PARTS:
        p = PKG_ROOT / rel
        if not p.exists():
            missing.append(rel)
            continue
        body = strip_frontmatter(p.read_text(encoding="utf-8")) if rel == "SKILL.md" \
            else p.read_text(encoding="utf-8")
        body = re.sub(r"^#\s+", "## ", body, flags=re.M)      # 主标题降一级
        chunks.append(f"# {title}\n\n{body.strip()}\n\n---\n\n")
    chunks.append("""# 第五部分 · 脚本清单

以下是配套脚本（在技能包的 `scripts/` 目录里），本文件不含源码：

| 脚本 | 用途 |
|---|---|
| `bootstrap.py` | 建虚拟环境、装依赖、下模型（换电脑第一步） |
| `doctor.py` | 体检：环境/依赖/模型/磁盘/网络逐项检查并给修复建议 |
| `init_meeting_workspace.py` | 建工作目录 + 开工确认单 |
| `transcribe.py` | 本地转写 + 说话人分离 + 术语纠错 |
| `diarize.py` | 单独调说话人分离（改人数/阈值） |
| `qc_check.py` | 六项质检 |
| `verify_refs.py` | 出处锚点对账，验证引用真伪 |
| `build_outputs.py` | 出 Word / HTML |
| `build_ppt.py` | 出 PPT（按需） |
| `append_checkpoint.py` | 追加节点记录 |
| `install_skill.py` | 装到 Codex 技能目录 |
| `make_single_file.py` | 生成本文件 |
| `make_package.py` | 生成分发包 |

没有脚本也能跑纯文字流程，只是转写要另想办法、质检要人工自查。
""")
    out = PKG_ROOT / OUT_NAME
    out.write_text("".join(chunks), encoding="utf-8")
    print(f"[生成] {out}（{out.stat().st_size / 1024:.1f} KB）")
    if missing:
        print(f"[警告] 缺少源文件：{'、'.join(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
