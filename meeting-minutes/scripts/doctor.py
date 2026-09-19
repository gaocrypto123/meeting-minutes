#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meeting-minutes v2 · 体检（换电脑、跑不动、报错时先跑这个）

把"能不能跑"拆成 8 项逐个检查，每项给出结论和修复建议。
**它自己不需要任何第三方依赖**，所以环境再坏也能跑。

用法：
    python scripts/doctor.py            # 完整体检
    python scripts/doctor.py --paths    # 只打印解析出来的路径
    python scripts/doctor.py --offline  # 跳过联网检查
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import PKG_ROOT, human, load_config  # noqa: E402

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
ICON = {PASS: "✅", WARN: "⚠️ ", FAIL: "❌"}

PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
MODEL_HOST = "https://hf-mirror.com"


def run_py(python: str, code: str, timeout: int = 40):
    """用指定解释器跑一段代码，返回 (ok, stdout)。"""
    try:
        p = subprocess.run([python, "-c", code], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode == 0, (p.stdout or "") + (p.stderr or "")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


MANUAL_CHANNELS = """三条拿稿通道（按推荐顺序）：
1. 会议平台自带纪要 —— 腾讯会议 / 飞书妙记 / 钉钉闪记 都自带「转写 + 自动纪要」，
   录完即可导出文字稿，准确率高于本地离线转写。这是最省事的路。
2. 手机录音机自带转写 —— iPhone 备忘录录音、小米/华为/OPPO 录音机大多有「转文字」。
3. 剪映 / 讯飞听见 / 网易见外 —— 导入音频导出字幕或文稿，多数有免费额度。"""


def decide_route(checks) -> dict:
    """把体检结果翻译成「走哪条路」的结论 —— 用结论取代试错。

    A 立即可转写 / B 需先装依赖或下模型 / C 本机不具备条件，转人工供稿。
    退出码 3 代表 C，AI 看到就该立刻转路 3，不要再尝试。
    """
    by = {c["name"]: c for c in checks}
    deps_ok = by.get("依赖包", {}).get("status") == PASS
    model_ok = by.get("转写模型", {}).get("status") == PASS
    net = by.get("联网", {})
    # 任一镜像可达即视为网络可用：bootstrap 内置多源会依次尝试，
    # 单个源探测失败（镜像常拒 HEAD/带 UA 限制）不代表装不上。
    net_ok = "✓" in net.get("detail", "")
    net_unknown = "跳过" in net.get("detail", "")

    if deps_ok and model_ok:
        return {"path": "A", "title": "路径 A · 立即可转写",
                "reason": "依赖与转写模型都已就位。",
                "action": "直接跑 transcribe.py，建议先试跑 1 分钟确认音质。",
                "channels": MANUAL_CHANNELS}
    if net_ok or net_unknown:
        need = []
        if not deps_ok:
            need.append("依赖")
        if not model_ok:
            need.append("模型")
        return {"path": "B", "title": "路径 B · 需先装依赖或下载模型",
                "reason": "网络可达，缺 " + "、".join(need) + "。",
                "action": "python scripts/bootstrap.py --install --models small；"
                          "装完再跑一次本命令确认。",
                "channels": MANUAL_CHANNELS}
    return {"path": "C", "title": "路径 C · 本机不具备转写条件，转人工供稿",
            "reason": "网络不可达，且依赖或模型缺失，无法安装本地转写引擎。",
            "action": "不要重试转写。请用户直接提供文字稿，见下方三条通道。",
            "channels": MANUAL_CHANNELS}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="meeting-minutes v3 环境体检（含路线结论）")
    ap.add_argument("--paths", action="store_true", help="只打印解析出的路径")
    ap.add_argument("--offline", action="store_true", help="跳过联网检查")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结论")
    args = ap.parse_args()

    cfg = load_config()
    home, models_dir = Path(cfg["home"]), Path(cfg["models_dir"])
    vpy, venv = Path(cfg["python"]), Path(cfg["venv"])

    if args.paths:
        for k in ("home", "models_dir", "envs_dir", "python", "glossary"):
            v = cfg.get(k, "")
            if k == "glossary" and v and not Path(v).is_absolute():
                v = str(PKG_ROOT / v)
            print(f"{k} = {v}")
        print(f"config = {cfg.get('_config_path')}")
        print(f"config.local = {cfg.get('_local_config') or '（无）'}")
        return 0

    checks = []

    def add(name, status, detail, fix=""):
        checks.append({"name": name, "status": status, "detail": detail, "fix": fix})

    # 1 运行时
    ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 9):
        status = PASS if sys.version_info < (3, 13) else WARN
        add("Python 版本", status, f"当前 {ver}",
            "3.13+ 部分依赖可能没有现成安装包，建议 3.10–3.12" if status == WARN else "")
    else:
        add("Python 版本", FAIL, f"当前 {ver}，需要 3.9 及以上",
            "到 python.org 或 miniconda 装一个 3.10–3.12")

    # 2 配置解析
    add("配置解析", PASS, f"home={home}（来源：{cfg.get('_config_path')}"
        + (f" + {Path(cfg['_local_config']).name}" if cfg.get("_local_config") else "") + "）")

    # 3 虚拟环境
    if vpy.exists():
        ok, out = run_py(str(vpy), "import sys;print(sys.version.split()[0])")
        add("虚拟环境", PASS if ok else FAIL,
            f"{vpy}" + (f"（{out.strip()[:40]}）" if ok else f" 无法运行：{out.strip()[:80]}"),
            "" if ok else "删掉后重跑 bootstrap.py --install")
    else:
        add("虚拟环境", FAIL, f"不存在：{vpy}",
            f"运行：python scripts/bootstrap.py --install --models small")

    # 4 依赖
    probe = ("import importlib.util as u;"
             "mods=['av','faster_whisper','sherpa_onnx','docx','pptx'];"
             "print(','.join(m for m in mods if not u.find_spec(m)))")
    if not vpy.exists():
        add("依赖包", FAIL, "无法检测——虚拟环境还没建",
            "运行：python scripts/bootstrap.py --install")
    else:
        ok, out = run_py(str(vpy), probe)
        missing = [m for m in (out.strip().splitlines() or [""])[0].split(",") if m]
        if ok and not missing:
            add("依赖包", PASS, "av / faster-whisper / sherpa-onnx / python-docx / python-pptx 齐全")
        else:
            add("依赖包", FAIL, "缺少：" + ("、".join(missing) or "无法检测"),
                "运行：python scripts/bootstrap.py --install")

    # 5 转写模型
    found = []
    for size in ("small", "medium", "large-v3", "base", "tiny"):
        d = models_dir / f"faster-whisper-{size}"
        if (d / "model.bin").exists():
            found.append(f"{size}（{human((d / 'model.bin').stat().st_size)}）")
    want = cfg["defaults"]["asr_model"]
    if found:
        has_want = any(f.startswith(want) for f in found)
        add("转写模型", PASS if has_want else WARN,
            f"已就位：{'、'.join(found)}；配置要求：{want}",
            "" if has_want else f"运行：python scripts/bootstrap.py --models {want}")
    else:
        add("转写模型", FAIL, f"没有找到任何模型（找的是 {models_dir}）",
            f"运行：python scripts/bootstrap.py --models {want}")

    # 6 说话人分离模型
    seg = Path(cfg["diarization"]["segmentation_model"])
    emb = Path(cfg["diarization"]["embedding_model"])
    if seg.exists() and emb.exists():
        add("说话人分离模型", PASS, f"{human(seg.stat().st_size)} + {human(emb.stat().st_size)}")
    else:
        add("说话人分离模型", WARN, "缺少分割或声纹模型，转写仍可跑，但不会有发言人编号",
            "运行：python scripts/bootstrap.py --with-diarization")

    # 7 磁盘
    probe_path = home if home.exists() else home.anchor or "."
    try:
        free = shutil.disk_usage(str(probe_path)).free
        if free >= 3 * 1024 ** 3:
            add("磁盘空间", PASS, f"{str(home)[:3]} 剩余 {human(free)}")
        else:
            add("磁盘空间", WARN, f"剩余 {human(free)}，装 medium 模型可能不够",
                "换到空间大的盘：python scripts/bootstrap.py --home D:\\AIModels")
    except Exception as e:
        add("磁盘空间", WARN, f"无法读取：{e}")

    # 8 联网
    if args.offline:
        add("联网", WARN, "已按 --offline 跳过")
    else:
        reach = []
        for url, label in ((PIP_INDEX, "pip 镜像"), (MODEL_HOST, "模型镜像")):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                urllib.request.urlopen(req, timeout=8)
                reach.append(f"{label}✓")
            except Exception as e:
                reach.append(f"{label}✗({type(e).__name__})")
        n_ok = sum(1 for r in reach if "✓" in r)
        if n_ok == len(reach):
            add("联网", PASS, "、".join(reach))
        elif n_ok == 0:
            add("联网", FAIL, "、".join(reach),
                "一个镜像都连不上：只能手动下载依赖与模型，"
                "见 安装说明.md 的『没有网络怎么办』；或直接转人工供稿路径")
        else:
            add("联网", WARN, "、".join(reach),
                "部分镜像不通不影响 —— bootstrap 会自动换源，"
                "只有全部不通才需要走人工供稿")

    route = decide_route(checks)

    if args.json:
        print(json.dumps({"checks": checks, "route": route}, ensure_ascii=False, indent=2))
        return 3 if route["path"] == "C" else 0

    print("=" * 66)
    print("meeting-minutes v2 · 环境体检")
    print("=" * 66)
    for c in checks:
        print(f"{ICON[c['status']]} {c['name']}：{c['detail']}")
        if c["fix"]:
            print(f"      → {c['fix']}")
    n_fail = sum(1 for c in checks if c["status"] == FAIL)
    n_warn = sum(1 for c in checks if c["status"] == WARN)
    print("-" * 66)
    if n_fail:
        print(f"体检：有 {n_fail} 项必须先解决。")
    elif n_warn:
        print(f"体检：可以跑，但有 {n_warn} 项需要注意（见上面的箭头）。")
    else:
        print("体检：全部就绪。")

    print("")
    print("=" * 66)
    print(f"路线结论：{route['title']}")
    print("=" * 66)
    print(f"  依据：{route['reason']}")
    print(f"  动作：{route['action']}")
    if route["path"] == "C":
        print("")
        print("  !! 本机不具备本地转写条件。不要反复尝试安装或换源，")
        print("  !! 也不要试图用浏览器去蹭在线转写网站（需登录/验证码/付费，成功率极低）。")
        print("")
        for ln in route["channels"].strip().splitlines():
            print("  " + ln)
        print("")
        print("  拿到文字稿后直接交给 AI，从 Phase 2 开始即可，转写这步可以整个跳过。")
    elif route["path"] == "B":
        print("")
        print("  若安装/下载始终不成功，不要死磕 —— 转路 3 用人工供稿更快。")

    return 3 if route["path"] == "C" else (1 if n_fail else 0)


if __name__ == "__main__":
    raise SystemExit(main())
