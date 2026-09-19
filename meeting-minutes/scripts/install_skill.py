#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本技能包安装/同步到 Codex 的技能目录，让它在新对话里能被自动识别。

安装位置：<CODEX_HOME>/skills/<技能名>（默认 ~/.codex/skills/）
技能名来自 SKILL.md 的 `name:` 字段，也可用 --name 指定（会同时改安装副本的目录名与字段）。

用法：
    python scripts/install_skill.py                     # 预览，不写文件
    python scripts/install_skill.py --install           # 安装
    python scripts/install_skill.py --install --force   # 已存在时逐文件覆盖（同步用）
    python scripts/install_skill.py --install --clean   # 先删掉目标目录再装（彻底重装）

    # 把另一个技能包装到本机（例如原版 v1），并做本机适配
    python scripts/install_skill.py --install --source <包目录> --name <名字> --adapt-v1

只复制文本与脚本，跳过缓存；不写注册表、不改 PATH。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode"}
SKIP_SUFFIX = {".pyc", ".pyo", ".log", ".tmp"}


def read_skill_name(pkg: Path) -> str:
    md = pkg / "SKILL.md"
    if md.exists():
        m = re.search(r"^name:\s*[\"']?([^\"'\n]+?)[\"']?\s*$",
                      md.read_text(encoding="utf-8"), flags=re.M)
        if m:
            return m.group(1).strip()
    return pkg.name


def rename_skill(pkg: Path, new_name: str) -> bool:
    """改 **安装副本** 的 SKILL.md name 字段（不动源目录）。"""
    md = pkg / "SKILL.md"
    if not md.exists():
        return False
    text = md.read_text(encoding="utf-8")
    new_text, n = re.subn(r"^name:.*$", f"name: {new_name}", text, count=1, flags=re.M)
    if n:
        md.write_text(new_text, encoding="utf-8")
        return True
    return False


def skills_dir() -> Path:
    home = os.environ.get("CODEX_HOME")
    base = Path(home) if home else Path.home() / ".codex"
    return base / "skills"


def _runtime_paths():
    """从配置里取本机的模型目录与解释器（不写死路径）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from mm_common import load_config
        cfg = load_config()
        return cfg["models_dir"], cfg["python"]
    except Exception:
        return "", ""


def env_note(models_dir: str, python_exe: str) -> str:
    py_line = (f"> 依赖已装好，请用这个解释器：`{python_exe}`，**不要再跑 pip install**。\n"
               if python_exe else "")
    model_line = (f"> 语音模型在本机：`{models_dir}`，跑转写时用 "
                  f"`--model-dir {Path(models_dir) / 'faster-whisper-medium'}`，不需要下载。\n"
                  if models_dir else "")
    return ("\n> **【本机环境说明 · 非原版内容】**\n" + py_line + model_line +
            "> 原版第 5 阶段要路由到 `tencent-docx` / `tencent-pptx`，"
            "本机若没有这两个技能，需要 Word/PPT 时得另想办法。\n")


def adapt_v1(dest: Path, models_dir: str = "", python_exe: str = "") -> None:
    """让原版 v1 在本机跑得起来，同时避免往 C 盘下载 1.5 GB 模型。

    只改安装副本：① transcribe.py 的默认模型目录指向本机模型仓库；
    ② SKILL.md 正文之前插一段本机环境说明。原版流程逻辑一行不动。
    """
    if not models_dir or not python_exe:
        md_, py_ = _runtime_paths()
        models_dir = models_dir or md_
        python_exe = python_exe or py_

    tp = dest / "scripts" / "transcribe.py"
    if tp.exists() and models_dir:
        t = tp.read_text(encoding="utf-8")
        # 用 lambda 替换：re.sub 的替换串会把 \A 当转义符，而模型目录里可能有 "D:\AIModels"
        t2 = re.sub(
            r'base = Path\.home\(\)\s*/\s*"\.workbuddy"\s*/\s*"binaries"\s*/\s*"models"',
            lambda m: f'base = Path(r"{models_dir}")', t)
        if t2 != t:
            tp.write_text(t2, encoding="utf-8")
            print(f"[适配] 默认模型目录 → {models_dir}（避免下载到 C 盘）")

    md = dest / "SKILL.md"
    if md.exists():
        text = md.read_text(encoding="utf-8")
        if "本机环境说明" not in text:
            parts = text.split("---", 2)
            note = env_note(models_dir, python_exe)
            text = ("---".join(parts[:2]) + "---" + note + parts[2]
                    if len(parts) >= 3 else note + text)
            md.write_text(text, encoding="utf-8")
            print("[适配] 已在 SKILL.md 顶部插入本机环境说明")


LOCAL_ONLY = {"config.local.json", "术语表.local.txt", "打包-敏感词.txt"}


def copy_tree(src: Path, dst: Path, include_local: bool = False) -> tuple:
    copied = skipped = 0
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if item.name in LOCAL_ONLY and not include_local:
            skipped += 1
            continue
        if item.suffix in SKIP_SUFFIX:
            skipped += 1
            continue
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        copied += 1
    return copied, skipped


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="安装 meeting-minutes 到 Codex 技能目录")
    ap.add_argument("--install", action="store_true", help="实际写入；不加则只预览")
    ap.add_argument("--name", default="", help="技能名（同时改安装副本的 name 字段）")
    ap.add_argument("--dest", default="", help="技能根目录，默认 ~/.codex/skills")
    ap.add_argument("--source", default="", help="要安装的技能包目录，默认本脚本所在包")
    ap.add_argument("--force", action="store_true", help="已存在时逐文件覆盖（不删目录）")
    ap.add_argument("--clean", action="store_true", help="覆盖前先删掉整个目标目录")
    ap.add_argument("--adapt-v1", action="store_true", help="原版 v1 适配：模型目录+环境说明")
    ap.add_argument("--include-local", action="store_true",
                    help="把 config.local.json / 术语表.local.txt 也复制过去"
                         "（装到本机自己用时需要；发给别人时不要加）")
    args = ap.parse_args()

    src = Path(args.source).expanduser().resolve() if args.source else PKG_ROOT
    if not (src / "SKILL.md").exists():
        print(f"[错误] {src} 里没有 SKILL.md，不是技能包。", file=sys.stderr)
        return 2

    current = read_skill_name(src)
    name = (args.name or current).strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name):
        print(f"[警告] 技能名「{name}」不是标准格式（建议小写字母、数字、连字符）。",
              file=sys.stderr)

    dest_root = Path(args.dest).expanduser() if args.dest else skills_dir()
    dest = dest_root / name

    print(f"技能包：{src}")
    print(f"当前名字：{current}")
    print(f"安装到：{dest}")
    print(f"目标根目录存在：{dest_root.exists()}")

    if not args.install:
        print("\n（预览模式，什么都没写。加 --install 才会安装。）")
        return 0

    if dest.exists():
        if not args.force:
            print(f"\n[中止] {dest} 已存在。用 --force 覆盖（同步），或换个 --name。",
                  file=sys.stderr)
            return 2
        if args.clean:
            shutil.rmtree(dest)
            print(f"[清理] 已移除旧的 {dest}")
        else:
            print(f"[同步] {dest} 已存在，逐文件覆盖（不会删除目录）")

    dest.mkdir(parents=True, exist_ok=True)
    copied, skipped = copy_tree(src, dest, include_local=args.include_local)
    if name != current and rename_skill(dest, name):
        print(f"[改名] 安装副本的 name 字段：{current} → {name}（源文件未改动）")
    if args.adapt_v1:
        adapt_v1(dest)
    print(f"\n[完成] 复制 {copied} 个文件（跳过本地私有文件/缓存 {skipped} 个）")
    print(f"        技能位置：{dest}")
    print(f"\n下次新开对话时，直接说「用 {name} 把这个录音整理成会议纪要」即可。")
    _, py = _runtime_paths()
    if py:
        print(f"注意：技能只是说明书；转写要用配置里的解释器：{py}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
