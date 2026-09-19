#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meeting-minutes · 环境体检（Phase 1 之前的必跑步骤）

目的：**在动手之前**就搞清楚本机到底能不能转写，能走哪条路。
不要让 AI 靠试错去猜——试错一轮就是十几分钟，用户等到崩溃。

输出三件事：
  1. 逐项体检结果（Python / pip 源 / 网络 / 依赖 / 本地模型）
  2. 一个明确结论：推荐路径 A / B / C
  3. 机器可读的 JSON（--json 输出路径），供 AI 直接读结论而不是猜

路径判定：
  A 直接转写   —— 依赖齐全 + 本地已有可用模型，立即可跑
  B 需下载模型 —— 依赖齐全/可装 + 网络通，下载约 N 分钟后可用
  C 转写不可用 —— 依赖装不上或网络不通。**此时不要重试，直接换人工供稿路径**

用法：
    python scripts/doctor.py
    python scripts/doctor.py --json doctor.json
    python scripts/doctor.py --size medium      # 指定想用的模型档位
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------------------------------------------------------------- 常量

# 模型档位：越大越准，但下载越慢。远距离录音至少 medium，近距离可用 small/base
SIZE_INFO = {
    "base":   {"mib": 138,  "note": "138 MiB · 近场清晰录音勉强可用，远场基本不可用"},
    "small":  {"mib": 461,  "note": "461 MiB · 近场可用，远场/远距离录音识别率差"},
    "medium": {"mib": 1457, "note": "1457 MiB · 推荐。远场/手机远距离录音的最低可用档"},
}
DEFAULT_SIZE = "medium"

# 下载源（实测 2026-09：hf-mirror 可用且支持 Range；官方源国内超时；
# ModelScope 返回无 Content-Length、不支持续传，仅作最后兜底）
MIRRORS = [
    "https://hf-mirror.com/Systran/faster-whisper-{size}/resolve/main/{name}",
    "https://hf-mirror.com/guillaumekln/faster-whisper-{size}/resolve/main/{name}",
    "https://huggingface.co/Systran/faster-whisper-{size}/resolve/main/{name}",
]
MODEL_MIN_BYTES = {"base": 130 * 1024 ** 2, "small": 440 * 1024 ** 2, "medium": 1400 * 1024 ** 2}

PIP_INDEX = "https://mirrors.cloud.tencent.com/pypi/simple"

LINES = []


def log(s=""):
    print(s)
    LINES.append(str(s))


def default_model_root():
    return Path.home() / ".workbuddy" / "binaries" / "models"


# ---------------------------------------------------------------- 体检项

def check_python():
    log(f"[Python] {sys.version.split()[0]}  {sys.executable}")
    return {"ok": True, "path": sys.executable, "version": sys.version.split()[0]}


def check_modules():
    res = {}
    for mod in ("av", "numpy", "faster_whisper"):
        try:
            __import__(mod)
            res[mod] = True
        except Exception:
            res[mod] = False
    missing = [m for m, ok in res.items() if not ok]
    log(f"[依赖] av={res['av']} numpy={res['numpy']} faster_whisper={res['faster_whisper']}")
    if missing:
        log(f"       缺失：{', '.join(missing)}")
    return {"ok": not missing, "detail": res, "missing": missing}


def _head(url, timeout=10):
    """返回 (ok, detail)。只探连通性，不下数据。"""
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            clen = r.headers.get("Content-Length")
            return True, f"HTTP {r.status} · {time.time() - t0:.1f}s" + (
                f" · {int(clen) / 2 ** 20:.0f} MiB · Range={r.headers.get('Accept-Ranges')}"
                if clen else "")
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:60]}"


def check_network():
    """探 pip 源 + 模型源。返回各源状态与最优可用源。"""
    log("[网络] 探测中（每项最多 10 秒）…")
    out = {"pip": None, "mirrors": [], "best": None}

    ok, detail = _head(PIP_INDEX)
    out["pip"] = {"ok": ok, "detail": detail}
    log(f"       pip 源 {PIP_INDEX.split('/')[2]:28s} {'可达' if ok else '不可达'}  {detail}")

    for tpl in MIRRORS:
        url = tpl.format(size="small", name="config.json")
        ok, detail = _head(url)
        host = url.split("/")[2]
        out["mirrors"].append({"url": tpl, "ok": ok, "detail": detail, "host": host})
        log(f"       模型源 {host:28s} {'可达' if ok else '不可达'}  {detail}")
        if ok and out["best"] is None:
            out["best"] = tpl
    out["ok"] = out["best"] is not None
    return out


def check_local_models():
    root = default_model_root()
    found = {}
    for size, need in MODEL_MIN_BYTES.items():
        d = root / f"faster-whisper-{size}"
        mb = d / "model.bin"
        ok = mb.exists() and mb.stat().st_size >= need
        if mb.exists():
            found[size] = {"dir": str(d), "ok": ok,
                           "mib": round(mb.stat().st_size / 2 ** 20)}
        else:
            found[size] = {"dir": str(d), "ok": False, "mib": 0}
    log(f"[模型] 根目录 {root}")
    for size in ("base", "small", "medium"):
        f = found[size]
        flag = "可用" if f["ok"] else ("不完整" if f["mib"] else "未下载")
        log(f"       {size:7s} {flag:6s} {f['mib']} MiB")
    return {"root": str(root), "models": found,
            "ready": [s for s, f in found.items() if f["ok"]]}


def check_ffmpeg():
    """ffmpeg 不是必需的（用 av 解码），但探一下便于排错。"""
    for cmd in ("ffmpeg",):
        try:
            r = subprocess.run([cmd, "-version"], capture_output=True, timeout=8)
            if r.returncode == 0:
                log(f"[ffmpeg] 已安装")
                return {"ok": True}
        except Exception:
            pass
    log("[ffmpeg] 未安装（不影响，脚本用 av 库解码）")
    return {"ok": False}


# ---------------------------------------------------------------- 结论

def decide(size, py, mods, net, models):
    """产出路径结论。这是本脚本唯一重要的输出。"""
    ready = models["ready"]
    if mods["ok"] and size in ready:
        return {
            "path": "A",
            "title": "路径 A · 立即可转写",
            "action": f"直接跑：python scripts/transcribe.py <音频> --out 01-转写稿.md --model {size}",
            "reason": f"依赖齐全，且本地已有可用的 {size} 模型。",
            "blocking": False,
        }
    if size in ready and not mods["ok"]:
        if net["pip"]["ok"]:
            return {
                "path": "A",
                "title": "路径 A · 装完依赖即可转写",
                "action": (f"{sys.executable} -m pip install -i {PIP_INDEX} "
                           f"{' '.join(mods['missing'])} 然后跑 transcribe.py"),
                "reason": f"模型已就位（{size}），只缺 Python 依赖，pip 源可达。",
                "blocking": False,
            }
    if not net["ok"]:
        return {
            "path": "C",
            "title": "路径 C · 转写不可用，改走人工供稿",
            "action": "不要重试转写。请用户直接提供文字稿，见下方三条拿稿通道。",
            "reason": "所有模型下载源均不可达，本机无法安装转写引擎。",
            "blocking": True,
        }
    if not net["pip"]["ok"] and mods["missing"]:
        return {
            "path": "C",
            "title": "路径 C · 转写不可用，改走人工供稿",
            "action": "不要重试转写。请用户直接提供文字稿，见下方三条拿稿通道。",
            "reason": "pip 源不可达且依赖缺失，无法安装转写引擎。",
            "blocking": True,
        }
    mib = SIZE_INFO.get(size, {}).get("mib", 0)
    return {
        "path": "B",
        "title": "路径 B · 需先下载模型",
        "action": (f"python scripts/transcribe.py <音频> --out 01-转写稿.md --model {size}"
                   f"（脚本会自动下载约 {mib} MiB，断点续传）"),
        "reason": f"网络可达，需下载 {size} 模型（约 {mib} MiB）。",
        "blocking": False,
        "download_mib": mib,
    }


MANUAL_CHANNELS = """三条拿稿通道（按推荐顺序）：
  1. 会议平台自带纪要 —— 腾讯会议 / 飞书妙记 / 钉钉闪记 都自带「转写 + 自动纪要」，
     录完即可导出文字稿，准确率远高于本地离线转写。这是最省事的路。
  2. 手机录音机自带转写 —— iPhone 备忘录录音、小米/华为/OPPO 录音机大多有「转文字」，
     导出后直接给 AI。
  3. 剪映 / 讯飞听见 / 网易见外 —— 导入音频导出字幕或文稿，多数有免费额度。

拿到文字稿后直接交给 AI，从 Phase 2 开始即可，转写这步可以整个跳过。
"""


def main():
    ap = argparse.ArgumentParser(description="meeting-minutes 环境体检")
    ap.add_argument("--size", default=DEFAULT_SIZE, choices=list(SIZE_INFO),
                    help=f"希望使用的模型档位，默认 {DEFAULT_SIZE}")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="把结构化结论写到指定 JSON 文件，供 AI 读取")
    ap.add_argument("--skip-network", action="store_true", help="跳过网络探测（离线体检）")
    args = ap.parse_args()

    log("=" * 66)
    log(" meeting-minutes · 环境体检")
    log(f" 目标模型档位：{args.size}  —— {SIZE_INFO[args.size]['note']}")
    log("=" * 66)
    log("")

    py = check_python()
    mods = check_modules()
    ffmpeg = check_ffmpeg()
    if args.skip_network:
        net = {"ok": False, "pip": {"ok": False, "detail": "已跳过"}, "mirrors": [], "best": None}
        log("[网络] 已按 --skip-network 跳过")
    else:
        net = check_network()
    models = check_local_models()
    log("")

    verdict = decide(args.size, py, mods, net, models)

    log("=" * 66)
    log(f" 结论：{verdict['title']}")
    log("=" * 66)
    log(f" 依据：{verdict['reason']}")
    log(f" 动作：{verdict['action']}")
    log("")
    if verdict["path"] == "C":
        log("  !! 本机不具备本地转写条件。不要反复尝试下载或换源，")
        log("  !! 也不要试图用浏览器去蹭在线转写网站（需登录/验证码/付费，成功率极低）。")
        log("")
        for ln in MANUAL_CHANNELS.strip().splitlines():
            log("  " + ln)
    elif verdict["path"] == "B":
        log(f"  预计下载 {verdict.get('download_mib', 0)} MiB。若网络慢，可先试小档：")
        log("    --model base   （138 MiB，近场录音够用，远场会明显变差）")
        log("    --model small  （461 MiB，折中）")
        log("  中断后重跑会自动续传，不会白下。")
    else:
        log("  建议先用 --limit 60 试跑前 60 秒确认音质，再跑全量。")

    result = {
        "python": py, "modules": mods, "ffmpeg": ffmpeg, "network": net,
        "models": models, "size": args.size, "verdict": verdict,
        "manual_channels": MANUAL_CHANNELS,
    }
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log("")
        log(f"[JSON] 结构化结论已写入 {p}")
    return 0 if not verdict["blocking"] else 3


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
