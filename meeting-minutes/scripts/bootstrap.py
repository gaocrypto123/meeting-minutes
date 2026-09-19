#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meeting-minutes v2 · 环境与模型仓库引导（幂等，可反复运行）

把本技能需要的全部“重资产”装进 --home 指定的**一个**文件夹（默认 D:\\AIModels）：

    <home>/envs/meeting-minutes/   独立 Python 虚拟环境（不动你原有的 Python）
    <home>/models/                 模型仓库（以后别的工具也可以指定用这里）
    <home>/logs/install-log.txt    安装日志
    <home>/cache/                  pip 缓存（刻意留在 D 盘，不吃 C 盘）
    <home>/registry.json           模型登记表
    <home>/README.md               这里装了什么、多大、怎么彻底删掉

除 <home> 这一个文件夹外，不改动系统任何位置：
不写注册表、不改 PATH、不往 C 盘装东西、不碰你现有的 Python 环境。

用法：
    python bootstrap.py                  只体检，不安装
    python bootstrap.py --install        建目录 + 虚拟环境 + 装依赖
    python bootstrap.py --install --models small
    python bootstrap.py --install --models small --with-diarization
    python bootstrap.py --status
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import (  # noqa: E402
    FALLBACK_HOME, Log, build_registry, dir_size, ensure_ssl_or_reexec,
    fetch, human, load_config, runtime_env,
)

PIP_PACKAGES = [
    "av",
    "faster-whisper",
    "sherpa-onnx",
    "python-docx",
    "python-pptx",
]

PIP_MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.cloud.tencent.com/pypi/simple",
    "https://pypi.org/simple",
]

ASR_MIRRORS = [
    "https://hf-mirror.com/Systran/faster-whisper-{size}/resolve/main/{name}",
    "https://huggingface.co/Systran/faster-whisper-{size}/resolve/main/{name}",
]
ASR_FILES = ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]
ASR_MIN_MIB = {"tiny": 60, "base": 120, "small": 400, "medium": 1200}

DIAR_MODELS = [
    {
        "key": "seg",
        "desc": "说话人分割 pyannote-segmentation-3.0",
        "rel": "sherpa-segmentation-3.0/model.onnx",
        "min_bytes": 4 * 1024 * 1024,
        "urls": [
            "https://hf-mirror.com/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/main/model.onnx",
            "https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/main/model.onnx",
        ],
    },
    {
        "key": "emb",
        "desc": "声纹嵌入 3D-Speaker eres2net（中文）",
        "rel": "sherpa-embedding-zh/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
        "min_bytes": 10 * 1024 * 1024,
        "urls": [
            "https://hf-mirror.com/csukuangfj/speaker-embedding-models/resolve/main/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
            "https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/main/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
        ],
    },
]

README_TEMPLATE = """# AIModels · meeting-minutes 的重资产仓库

这个文件夹是本技能包在系统里**唯一**新增的位置，里面放两类东西：

- `envs\\meeting-minutes\\` —— 独立 Python 虚拟环境（依赖都在这里，没有装进你原有的 Python）
- `models\\` —— 模型仓库（语音转写 + 说话人分离），以后别的工具也可以指定用这里

## 想彻底删掉怎么办

直接删除整个 `{home}` 文件夹即可，电脑恢复原样。
没有任何残留：不写注册表、不改 PATH、不往 C 盘装东西。

## 现在有什么

{inventory}

## 日志

每次安装与下载都会追加记录到 `logs\\install-log.txt`，可以照着它逐条还原。

生成时间：{now}
"""


def run(cmd, log, env=None) -> int:
    log("  $ " + " ".join(str(c) for c in cmd))
    p = subprocess.Popen(
        [str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env,
    )
    assert p.stdout is not None
    for line in p.stdout:
        line = line.rstrip()
        if line:
            log("    " + line)
    return p.wait()


def ensure_dirs(home: Path, log) -> None:
    for sub in ("envs", "models", "logs", "cache/pip", "tmp"):
        d = home / sub
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            log(f"[新建] {d}")


def venv_python(home: Path) -> Path:
    return home / "envs" / "meeting-minutes" / "Scripts" / "python.exe"


SITECUSTOMIZE = '''# -*- coding: utf-8 -*-
"""由 meeting-minutes 的 bootstrap 自动生成。

conda 系 Python 的 OpenSSL 运行库放在 <prefix>/Library/bin，而该目录通常不在
PATH 上，导致 _ssl.pyd 加载失败、Python 丧失 HTTPS 能力。这里在解释器启动时
把它补进本进程的 PATH（只影响本虚拟环境，不改系统设置）。
"""
import os
import sys
from pathlib import Path

_bins = []
for _p in {sys.prefix, getattr(sys, "base_prefix", sys.prefix)}:
    _d = Path(_p) / "Library" / "bin"
    if _d.is_dir():
        _bins.append(str(_d))
if _bins:
    os.environ["PATH"] = os.pathsep.join(_bins + [os.environ.get("PATH", "")])
'''


def write_sitecustomize(home: Path, log) -> None:
    """让虚拟环境自带 HTTPS 能力（解决 conda Python 缺 OpenSSL 路径的问题）。"""
    sp = home / "envs" / "meeting-minutes" / "Lib" / "site-packages"
    if not sp.exists():
        return
    target = sp / "sitecustomize.py"
    if target.exists() and "meeting-minutes" in target.read_text(
            encoding="utf-8", errors="ignore"):
        return
    if target.exists():
        log(f"[跳过] {target} 已存在且不是本工具生成的，不覆盖")
        return
    target.write_text(SITECUSTOMIZE, encoding="utf-8")
    log(f"[修复] 已写入 {target}（让虚拟环境自带 HTTPS 能力）")


def ensure_venv(home: Path, base_python: str, log) -> bool:
    vpy = venv_python(home)
    if vpy.exists():
        log(f"[跳过] 虚拟环境已存在：{vpy}")
    else:
        log(f"[创建] 虚拟环境 → {home / 'envs' / 'meeting-minutes'}")
        log(f"       基于：{base_python}")
        code = run([base_python, "-m", "venv", str(home / "envs" / "meeting-minutes")],
                   log, env=runtime_env())
        if code != 0 or not vpy.exists():
            log("[失败] 虚拟环境创建失败")
            return False
    write_sitecustomize(home, log)
    return True


def pip_env(home: Path) -> dict:
    env = runtime_env()
    env["PIP_CACHE_DIR"] = str(home / "cache" / "pip")
    env["TMP"] = str(home / "tmp")
    env["TEMP"] = str(home / "tmp")
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    return env


def pip_install(home: Path, log) -> bool:
    vpy = venv_python(home)
    env = pip_env(home)
    lock = PKG_ROOT / "requirements-lock.txt"
    loose = PKG_ROOT / "requirements.txt"
    plans = []
    if lock.exists():
        plans.append(("锁定版本（已实测）", ["-r", str(lock)]))
    if loose.exists():
        plans.append(("宽松版本（兜底）", ["-r", str(loose)]))
    if not plans:
        plans.append(("内置清单", PIP_PACKAGES))

    for label, spec in plans:
        for mirror in PIP_MIRRORS:
            log(f"[安装依赖] {label} ← {mirror}")
            code = run([vpy, "-m", "pip", "install", "--no-warn-script-location",
                        "-i", mirror, *spec], log, env=env)
            if code == 0:
                log(f"[安装依赖] 完成（{label}）")
                return True
            log(f"[安装依赖] 失败（退出码 {code}），换下一个源")
        log(f"[安装依赖] {label} 所有源都失败，换下一套清单")
    log("[安装依赖] 全部失败")
    return False


def verify_env(home: Path, log) -> bool:
    vpy = venv_python(home)
    code = run([vpy, "-c", (
        "import importlib,sys\n"
        "print('python', sys.version.split()[0])\n"
        "for m in ['av','faster_whisper','ctranslate2','sherpa_onnx','docx','pptx','numpy']:\n"
        "    try:\n"
        "        mod=importlib.import_module(m)\n"
        "        print('  OK  ', m, getattr(mod,'__version__',''))\n"
        "    except Exception as e:\n"
        "        print('  MISS', m, type(e).__name__, e)\n"
    )], log, env=runtime_env())
    return code == 0


def download_asr(home: Path, size: str, log) -> bool:
    d = home / "models" / f"faster-whisper-{size}"
    d.mkdir(parents=True, exist_ok=True)
    need_min = ASR_MIN_MIB.get(size, 400) * 1024 * 1024
    mb = d / "model.bin"
    if mb.exists() and mb.stat().st_size >= need_min:
        log(f"[跳过] 转写模型已就绪：{d}（{human(mb.stat().st_size)}）")
        return True
    log(f"[下载] faster-whisper-{size} → {d}")
    for name in ASR_FILES:
        target = d / name
        expect = need_min if name == "model.bin" else 0
        if target.exists() and target.stat().st_size > 0:
            if name != "model.bin" or target.stat().st_size >= need_min:
                continue
        ok = False
        for tpl in ASR_MIRRORS:
            url = tpl.format(size=size, name=name)
            log(f"  ← {url.split('/')[2]}")
            if fetch(url, target, expect_min=expect, log=log):
                ok = True
                break
        if not ok:
            log(f"[下载失败] {name}；可手动下载后放到：{target}")
            return False
    try:
        json.loads((d / "config.json").read_text(encoding="utf-8"))
    except Exception:
        log("[模型损坏] config.json 无法解析，请删除该目录后重跑")
        return False
    return True


def download_diar(home: Path, log) -> bool:
    all_ok = True
    for spec in DIAR_MODELS:
        target = home / "models" / spec["rel"]
        if target.exists() and target.stat().st_size >= spec["min_bytes"]:
            log(f"[跳过] {spec['desc']} 已就绪：{target}")
            continue
        log(f"[下载] {spec['desc']}")
        ok = False
        for url in spec["urls"]:
            log(f"  ← {url.split('/')[2]}")
            if fetch(url, target, expect_min=spec["min_bytes"], log=log):
                ok = True
                break
        if not ok:
            log(f"[下载失败] {spec['desc']}。可手动下载后放到：{target}")
            all_ok = False
    return all_ok


def write_registry(home: Path, log) -> dict:
    reg = build_registry(home, home / "models")
    (home / "registry.json").write_text(
        json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"[写入] {home / 'registry.json'}（登记 {len(reg['models'])} 个模型）")
    return reg


def write_readme(home: Path, log) -> None:
    rows = []
    for name in ("envs", "models", "cache"):
        p = home / name
        if p.exists():
            rows.append(f"- `{name}\\` —— {human(dir_size(p))}")
    reg = home / "registry.json"
    if reg.exists():
        try:
            for m in json.loads(reg.read_text(encoding="utf-8")).get("models", []):
                rel = str(m.get("path", ""))
                if rel.startswith(str(home)):
                    rel = rel.replace(str(home), "").lstrip("\\")
                    rows.append(f"- `{rel}` —— {m.get('kind')} / {human(m.get('bytes'))}")
        except Exception:
            pass
    (home / "README.md").write_text(
        README_TEMPLATE.format(home=home, inventory="\n".join(rows) or "（空）",
                               now=datetime.now().strftime("%Y-%m-%d %H:%M")),
        encoding="utf-8")
    log(f"[写入] {home / 'README.md'}")


def status(home: Path, log) -> None:
    log(f"仓库位置：{home}   存在：{home.exists()}")
    if not home.exists():
        return
    for name in ("envs", "models", "cache", "tmp", "logs"):
        p = home / name
        log(f"  {name:<8} {human(dir_size(p)) if p.exists() else '（无）'}")
    log(f"  合计     {human(dir_size(home))}")
    vpy = venv_python(home)
    log(f"虚拟环境 Python：{vpy}")
    log(f"                 存在：{vpy.exists()}")
    for spec in DIAR_MODELS:
        p = home / "models" / spec["rel"]
        extra = f"（{human(p.stat().st_size)}）" if p.exists() else ""
        log(f"  {spec['key']:<4} 存在：{p.exists()} {extra}")


def cleanup_tmp(home: Path, log) -> None:
    tmp = home / "tmp"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
        log(f"[清理] 已清空下载临时目录 {tmp}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ensure_ssl_or_reexec()

    ap = argparse.ArgumentParser(description="meeting-minutes v2 环境引导")
    ap.add_argument("--home", default="", help="重资产目录，默认读 config.json 的 home")
    ap.add_argument("--base-python", default=sys.executable,
                    help="用于创建虚拟环境的 Python（默认当前解释器）")
    ap.add_argument("--install", action="store_true", help="建目录 + 虚拟环境 + 装依赖")
    ap.add_argument("--models", default="", help="下载转写模型，如 small 或 small,medium")
    ap.add_argument("--with-diarization", action="store_true", help="下载说话人分离模型")
    ap.add_argument("--status", action="store_true", help="只显示仓库现状")
    ap.add_argument("--config", default="", help="配置文件路径")
    args = ap.parse_args()

    cfg = load_config(args.config or None)
    if args.home:
        home = Path(args.home).expanduser()
    else:
        home = Path(cfg["home"])
        # 没指定过任何位置时问一句（同学可能只有 C 盘，也可能想放 D 盘）
        if home == FALLBACK_HOME and not os.environ.get("MM_HOME") and sys.stdin.isatty():
            print(f"\n依赖和模型会装到一个目录里（约 0.5–2.5 GB，可随时整个删掉）。")
            print(f"回车用默认位置：{home}")
            try:
                ans = input("也可以现在填别的盘，例如 D:\\AIModels ：").strip().strip('"')
            except (EOFError, KeyboardInterrupt):
                ans = ""
            if ans:
                home = Path(ans).expanduser()
    log = Log(home / "logs" / "install-log.txt")

    log("")
    log("=" * 68)
    log(f"meeting-minutes v2 · 环境引导   {datetime.now():%Y-%m-%d %H:%M:%S}")
    log(f"仓库位置：{home}")
    log(f"配置文件：{cfg.get('_config_path')}")
    log("=" * 68)

    if args.status:
        status(home, log)
        return 0

    if not (args.install or args.models or args.with_diarization):
        log("当前是体检模式（不改动任何东西）。加 --install 才会开始安装。")
        status(home, log)
        return 0

    ensure_dirs(home, log)
    ok = True

    if args.install:
        if not ensure_venv(home, args.base_python, log):
            return 2
        if not pip_install(home, log):
            ok = False
        verify_env(home, log)

    if args.models:
        for size in [s.strip() for s in args.models.split(",") if s.strip()]:
            if not download_asr(home, size, log):
                ok = False

    if args.with_diarization:
        if not download_diar(home, log):
            ok = False

    write_registry(home, log)
    write_readme(home, log)
    cleanup_tmp(home, log)
    status(home, log)

    log("")
    log(f"[结果] {'全部完成' if ok else '有步骤未成功，请看上面的日志'}")
    log(f"[还原] 删除 {home} 这一个文件夹即可恢复原样。")
    if venv_python(home).exists():
        log(f"[解释器] 之后所有脚本都用它：{venv_python(home)}")
    log("[自检] 建议再跑一次：python scripts/doctor.py")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[中断] 已停止。已下载的部分会保留，重跑会自动续传。")
        sys.exit(130)
