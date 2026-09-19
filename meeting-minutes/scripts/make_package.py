#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成可以发给别人的分发包（zip）。

做三件事：
  1. 按白名单复制文件（只带技能真正需要的东西，跳过所有本地/私有文件）
  2. 敏感信息扫描：发现绝对路径、发布者自己的用户名、API Key 特征就中止
  3. 打成 zip，压缩包里只有一个顶层文件夹，解压后直接看到 SKILL.md

用法：
    python scripts/make_package.py                 # 生成到 <技能包>/../dist/
    python scripts/make_package.py --out D:\\dist
    python scripts/make_package.py --name meeting-minutes-v2.0

私有词表：把不想外传的词（客户名、项目名）一行一个写进
`打包-敏感词.txt`（放在技能包根目录，本文件不会被打进包里）。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent

# 只带这些（文件或目录）
INCLUDE = [
    "SKILL.md",
    "说明书.md",
    "安装说明.md",
    "单文件版-可直接粘贴给AI.md",
    "config.json",
    "术语表.txt",
    "requirements.txt",
    "requirements-lock.txt",
    "references",
    "scripts",
]

# 明确不带（本地/私有/中间产物）
EXCLUDE_NAMES = {
    "config.local.json", "术语表.local.txt", "打包-敏感词.txt",
    "__pycache__", ".git", ".idea", ".vscode", "dist",
}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".log", ".tmp", ".part"}

SENSITIVE_LOCAL_FILE = "打包-敏感词.txt"


def generic_patterns():
    """不写死任何个人信息，运行时推导。"""
    pats = [
        (r"[A-Za-z]:\\Users\\", "Windows 用户目录绝对路径"),
        (r"[A-Za-z]:\\(?!AIModels)[A-Za-z0-9_\-]+\\", "其它 Windows 绝对路径"),
        (r"/(Users|home)/[A-Za-z0-9._\-]+", "类 Unix 用户目录路径"),
        (r"\bsk-[A-Za-z0-9_\-]{12,}", "疑似 OpenAI API Key"),
        (r"\bhf_[A-Za-z0-9]{20,}", "疑似 HuggingFace Token"),
        (r"\bAKIA[0-9A-Z]{16}\b", "疑似 AWS Access Key"),
    ]
    name = Path.home().name
    if name:
        pats.append((re.escape(name), "发布者自己的用户名"))
    return pats


def load_local_terms() -> list:
    f = PKG_ROOT / SENSITIVE_LOCAL_FILE
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def iter_files(src: Path):
    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        if any(part in EXCLUDE_NAMES for part in rel.parts):
            continue
        if item.suffix in EXCLUDE_SUFFIX:
            continue
        if item.is_file():
            yield rel, item


def scan(stage: Path, extra_terms: list) -> list:
    """扫描待打包内容，返回问题列表。"""
    pats = [(re.compile(p), why) for p, why in generic_patterns()]
    pats += [(re.compile(re.escape(t)), "私有词表命中") for t in extra_terms]
    problems = []
    for rel, path in iter_files(stage):
        if path.suffix.lower() not in (".md", ".txt", ".json", ".py", ".cmd", ".ps1", ""):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            for pat, why in pats:
                m = pat.search(line)
                if m:
                    problems.append({"file": str(rel), "line": i, "why": why,
                                     "hit": m.group(0)[:40]})
    return problems


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="生成 meeting-minutes-v2 分发包")
    ap.add_argument("--out", default="", help="输出目录，默认 <技能包上级>/dist")
    ap.add_argument("--name", default="meeting-minutes-v2.0", help="包名（决定 zip 文件名）")
    ap.add_argument("--folder", default="", help="压缩包里的顶层文件夹名，默认同 --name")
    ap.add_argument("--include-local", action="store_true",
                    help="连 config.local.json / 术语表.local.txt 一起打（默认不打）")
    ap.add_argument("--skip-scan", action="store_true", help="跳过敏感信息扫描（不推荐）")
    args = ap.parse_args()

    # 先刷新单文件版
    try:
        sys.path.insert(0, str(PKG_ROOT / "scripts"))
        import make_single_file
        make_single_file.main()
    except Exception as e:
        print(f"[警告] 单文件版生成失败：{e}")

    out_dir = Path(args.out).expanduser() if args.out else PKG_ROOT.parent / "dist"
    folder = args.folder or args.name
    stage = out_dir / "_stage" / folder
    if stage.parent.exists():
        shutil.rmtree(stage.parent)
    stage.mkdir(parents=True, exist_ok=True)

    copied = 0
    skip = set() if args.include_local else {"config.local.json", "术语表.local.txt"}
    for name in INCLUDE:
        src = PKG_ROOT / name
        if not src.exists():
            continue
        if src.is_file():
            if src.name in skip:
                continue
            shutil.copy2(src, stage / name)
            copied += 1
        else:
            for rel, f in iter_files(src):
                if f.name in skip:
                    continue
                dst = stage / name / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
                copied += 1

    print(f"[暂存] {copied} 个文件 → {stage}")

    if not args.skip_scan:
        problems = scan(stage, load_local_terms())
        if problems:
            print(f"\n[中止] 发现 {len(problems)} 处可能的敏感信息，"
                  f"没有生成压缩包：")
            for p in problems[:30]:
                print(f"  {p['file']}:{p['line']}  [{p['why']}] {p['hit']}")
            if len(problems) > 30:
                print(f"  …… 另有 {len(problems) - 30} 处")
            print("\n修完再跑；确认无误可以用 --skip-scan（不推荐）。")
            return 2
        terms = load_local_terms()
        print(f"[扫描] 通过（通用规则 + 私有词表 {len(terms)} 条）")

    zip_path = out_dir / f"{args.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, f in iter_files(stage):
            z.write(f, Path(folder) / rel)
    size = zip_path.stat().st_size / 1024
    print(f"\n[完成] {zip_path}（{size:.1f} KB）")
    print(f"        解压后顶层就是 {folder}\\，里面直接是 SKILL.md")
    print(f"[清理] 已删除暂存目录 {stage.parent}")
    shutil.rmtree(stage.parent, ignore_errors=True)
    print(f"[时间] {datetime.now():%Y-%m-%d %H:%M}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
