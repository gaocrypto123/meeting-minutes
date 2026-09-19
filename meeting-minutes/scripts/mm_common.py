#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meeting-minutes v2 · 公共工具：配置、日志、音频解码、下载、模型仓库扫描。

设计原则：本文件不假设任何“重资产”的位置，全部从 config.json 读取，
便于换机器、换目录、换模型仓库。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
FALLBACK_HOME = Path.home() / ".meeting-minutes"

DEFAULTS = {
    "home": "",                     # 空 = 用 FALLBACK_HOME，或由 MM_HOME 覆盖
    "models_dir": "",               # 空 = <home>/models；也可写绝对路径指向已有模型仓库
    "envs_dir": "",                 # 空 = <home>/envs
    "defaults": {
        "asr_model": "small",
        "language": "zh",
        "chunk_seconds": 120,
        "chunk_overlap_seconds": 5,
        "threads": 8,
        "vad": True,
    },
    "diarization": {
        "enabled": True,
        "segmentation_model": r"sherpa-segmentation-3.0\model.onnx",
        "embedding_model": (r"sherpa-embedding-zh"
                            r"\3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"),
    },
    "outputs": {"default_choice": "ask"},
}

LOCAL_CONFIG_NAME = "config.local.json"      # 本机覆盖用，不随分发包走
LOCAL_GLOSSARY_NAME = "术语表.local.txt"


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def find_config(explicit=None):
    """按优先级找配置：显式参数 > 环境变量 MM_CONFIG > 包内 config.json。"""
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("MM_CONFIG"):
        cands.append(Path(os.environ["MM_CONFIG"]))
    cands.append(PKG_ROOT / "config.json")
    for c in cands:
        if c.exists():
            return c
    return None


def _resolve_path(value, base: Path):
    """相对路径按 base 解析；空值返回 None。"""
    if value is None or str(value).strip() == "":
        return None
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else (base / p)


def resolve_home(raw: dict) -> Path:
    """决定"重资产"目录，优先级：MM_HOME 环境变量 > config 里的 home > ~/.meeting-minutes。"""
    env = os.environ.get("MM_HOME")
    if env and env.strip():
        return Path(env.strip()).expanduser()
    h = str(raw.get("home") or "").strip()
    if h and h.lower() not in ("auto", "."):
        return Path(h).expanduser()
    return FALLBACK_HOME


def load_config(explicit=None) -> dict:
    path = find_config(explicit)
    raw, local = {}, {}
    if path:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[警告] 配置文件无法解析：{path}（{e}）。改用内置默认值。")
    local_path = PKG_ROOT / LOCAL_CONFIG_NAME
    if local_path.exists():
        try:
            local = json.loads(local_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[警告] 本地配置无法解析：{local_path}（{e}）。已忽略。")

    cfg = _deep_merge(DEFAULTS, raw)
    cfg = _deep_merge(cfg, local)          # 本机覆盖优先

    home = resolve_home(local or raw)
    cfg["home"] = str(home)
    models_dir = _resolve_path(cfg.get("models_dir"), home) or (home / "models")
    envs_dir = _resolve_path(cfg.get("envs_dir"), home) or (home / "envs")
    cfg["models_dir"] = str(models_dir)
    cfg["envs_dir"] = str(envs_dir)
    cfg["venv"] = str(envs_dir / "meeting-minutes")
    cfg["python"] = str(envs_dir / "meeting-minutes" / "Scripts" / "python.exe")

    # 说话人分离模型：相对路径按模型目录解析
    for key in ("segmentation_model", "embedding_model"):
        p = _resolve_path(cfg["diarization"].get(key), models_dir)
        cfg["diarization"][key] = str(p) if p else ""

    cfg["_config_path"] = str(path) if path else "（内置默认值）"
    cfg["_local_config"] = str(local_path) if local else ""
    return cfg


def hms(sec) -> str:
    sec = int(max(0, sec or 0))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def dir_size(path) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def human(nbytes) -> str:
    n = float(nbytes or 0)
    if n < 1024:
        return f"{int(n)} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
    return f"{n:.1f} GB"


def conda_lib_bins() -> list:
    """conda 系 Python 把 OpenSSL 等运行库放在 <prefix>/Library/bin。
    该目录不在 PATH 上时，_ssl.pyd 会加载失败，Python 就丧失 HTTPS 能力。"""
    out = []
    for prefix in {sys.prefix, getattr(sys, "base_prefix", sys.prefix)}:
        p = Path(prefix) / "Library" / "bin"
        if p.is_dir():
            out.append(str(p))
    return out


def runtime_env(extra_bins=None) -> dict:
    """构造给子进程用的环境：把虚拟环境 Scripts、conda 的 Library\\bin
    依次放到 PATH 最前面，避免出现 'SSL module is not available'。"""
    env = dict(os.environ)
    bins = [str(Path(sys.executable).parent)] if sys.executable else []
    bins += list(extra_bins or [])
    bins += conda_lib_bins()
    ordered, seen = [], set()
    for b in bins:
        if b and b not in seen:
            seen.add(b)
            ordered.append(b)
    if ordered:
        env["PATH"] = os.pathsep.join(ordered + [env.get("PATH", "")])
    return env


def ssl_ok() -> bool:
    try:
        import ssl  # noqa: F401
        return True
    except Exception:
        return False


def ensure_ssl_or_reexec(log=print) -> None:
    """本机 Python 若缺少 OpenSSL 运行库，就把 conda 的 Library\\bin 加进 PATH
    并重启自己一次，使 HTTPS（pip / 模型下载）可用。

    只影响当前进程与它启动的子进程，不改动系统设置、不往 C 盘写任何东西。
    不需要联网的脚本（转写、说话人分离）可以不调用它。
    """
    import subprocess

    if ssl_ok():
        return
    if os.environ.get("MM_SSL_RETRY") == "1":
        log("[致命] 补齐 PATH 后仍然没有 SSL，请检查当前 Python 安装是否完整。")
        sys.exit(3)
    bins = conda_lib_bins()
    if not bins:
        log("[致命] 当前 Python 缺少 SSL，且未找到可补齐的目录，无法联网。")
        sys.exit(3)
    log("[修复] 当前 Python 缺少 OpenSSL 运行库，已把以下目录加入 PATH 后重启：")
    for b in bins:
        log(f"        {b}")
    env = runtime_env()
    env["MM_SSL_RETRY"] = "1"
    script = sys.argv[0] if Path(sys.argv[0]).exists() else str(Path(__file__).resolve())
    sys.exit(subprocess.call([sys.executable, script, *sys.argv[1:]], env=env))


class Log:
    """同时打印到屏幕和日志文件（日志文件可选）。"""

    def __init__(self, path=None, echo: bool = True):
        self.path = Path(path) if path else None
        self.echo = echo
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, msg=""):
        line = str(msg)
        if self.echo:
            print(line, flush=True)
        if self.path:
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {line}\n")
            except OSError:
                pass


def decode_audio(path, start: float = 0.0, duration: float = 0.0, log=print):
    """解码为 16k 单声道 float32 并归一化。返回 (samples, meta)。"""
    import av
    import numpy as np

    container = av.open(str(path))
    stream = container.streams.audio[0]
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    chunks = []
    for frame in container.decode(stream):
        for f in resampler.resample(frame):
            chunks.append(f.to_ndarray().reshape(-1))
    for f in resampler.resample(None):
        chunks.append(f.to_ndarray().reshape(-1))
    container.close()

    if not chunks:
        raise RuntimeError(f"音频解码后为空：{path}")
    a = np.concatenate(chunks).astype("float32") / 32768.0
    full_dur = len(a) / 16000

    if start and start > 0:
        a = a[int(start * 16000):]
    if duration and duration > 0:
        a = a[: int(duration * 16000)]

    rms = float(((a ** 2).mean()) ** 0.5) if len(a) else 0.0
    peak = float(abs(a).max()) if len(a) else 0.0
    if peak > 0:
        a = a * (0.95 / peak)

    meta = {
        "full_duration": round(full_dur, 2),
        "slice_start": round(start or 0.0, 2),
        "duration": round(len(a) / 16000, 2),
        "rms": round(rms, 4),
        "peak": round(peak, 3),
    }
    log(f"[音频] 源时长 {hms(full_dur)}，本次处理 {hms(meta['duration'])}"
        f"（起点 {hms(meta['slice_start'])}）  RMS={rms:.4f} peak={peak:.3f} 已归一化")
    if rms < 0.01:
        log("[警告] 音量过低（RMS < 0.01），转写准确率会很差，建议换更近的录音。")
    return a, meta


def fetch(url: str, dst, expect_min: int = 0, resume: bool = True,
          tries: int = 6, timeout: int = 60, log=print) -> bool:
    """带 Range 断点续传的下载。expect_min 为最低可接受字节数。"""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = Path(str(dst) + ".part")
    for attempt in range(1, tries + 1):
        try:
            have = part.stat().st_size if part.exists() else 0
            headers = {"User-Agent": "Mozilla/5.0"}
            if have and resume:
                headers["Range"] = f"bytes={have}-"
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=timeout)
            code = resp.status
            clen = int(resp.headers.get("Content-Length", 0))
            total = have + clen
            mode = "ab" if (have and code == 206) else "wb"
            if mode == "wb":
                have, total = 0, clen
            log(f"  {dst.name}：第 {attempt} 次，已有 {human(have)} / 共 {human(total)}")
            t0 = last = time.time()
            with open(part, mode) as fh:
                while True:
                    buf = resp.read(512 * 1024)
                    if not buf:
                        break
                    fh.write(buf)
                    have += len(buf)
                    now = time.time()
                    if now - last > 20:
                        last = now
                        log(f"    {human(have)} / {human(total)}"
                            f"  ({have / 1048576 / max(0.1, now - t0):.1f} MiB/s)")
            resp.close()
            if expect_min and have < expect_min:
                log(f"  大小不足（{human(have)} < {human(expect_min)}），重试")
                time.sleep(3)
                continue
            part.replace(dst)
            log(f"  完成 {dst.name}  {human(dst.stat().st_size)}  用时 {time.time() - t0:.0f}s")
            return True
        except Exception as e:
            log(f"  异常 {type(e).__name__}: {e}（第 {attempt} 次）")
            time.sleep(3)
    return False


ASR_SIGNATURE = ("model.bin", "config.json", "tokenizer.json")


# ---------------------------------------------------------------- 章节模板

_TEMPLATE_RE = re.compile(r"^###\s*([A-E])[.、]\s*(.+)$")
_TEMPLATE_CACHE = {}

# 读不到 references/templates.md 时的兜底，保证脚本仍能跑
FALLBACK_TEMPLATES = {
    "跨部门对接会": ["会议背景", "议题与讨论要点", "意见总结", "行动项", "待确认与风险"],
    "内部工作会": ["会议背景", "进展同步", "问题与阻塞", "意见总结", "行动项与下周计划"],
    "决策评审会": ["会议背景与评审对象", "评审意见", "决策结论", "行动项", "待确认与风险"],
    "外部沟通会": ["会议背景", "客户需求与反馈", "我方回应与承诺", "行动项", "待确认与风险"],
    "采访访谈": ["采访背景", "受访者情况与观点", "关键信息与线索", "后续行动", "待确认与风险"],
}

TEMPLATE_ALIASES = {
    "跨部门": "跨部门对接会", "对接": "跨部门对接会", "跨部门对接": "跨部门对接会",
    "内部": "内部工作会", "工作会": "内部工作会", "例会": "内部工作会", "周会": "内部工作会",
    "决策": "决策评审会", "评审": "决策评审会", "决策评审": "决策评审会",
    "外部": "外部沟通会", "客户": "外部沟通会", "外部沟通": "外部沟通会",
    "采访": "采访访谈", "访谈": "采访访谈", "专访": "采访访谈", "调研": "采访访谈",
}


def load_templates(refresh: bool = False) -> dict:
    """从 references/templates.md 读章节骨架：{模板名: [一级章节标题, ...]}。

    模板名取自 `### X. 名字（N 章）`，章节取自该节代码块里的 `## N. 标题`。
    这样"加模板"只需要改 Markdown，不用再改脚本。
    """
    if _TEMPLATE_CACHE and not refresh:
        return _TEMPLATE_CACHE
    path = PKG_ROOT / "references" / "templates.md"
    blocks, cur, in_fence = {}, None, False
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("```"):
                in_fence = not in_fence
                continue
            m = _TEMPLATE_RE.match(s)
            if m and not in_fence:
                cur = re.sub(r"（.*?）\s*$", "", m.group(2)).strip()
                blocks.setdefault(cur, [])
                continue
            if s.startswith("## "):
                if in_fence and cur:
                    title = re.sub(r"^\d+[.、]\s*", "", s[3:].strip())
                    if title:
                        blocks[cur].append(title)
                else:
                    cur = None          # 代码块外的 ## 是文档章节，不是模板章节
    blocks = {k: v for k, v in blocks.items() if v} or dict(FALLBACK_TEMPLATES)
    _TEMPLATE_CACHE.clear()
    _TEMPLATE_CACHE.update(blocks)
    return _TEMPLATE_CACHE


def resolve_template(name: str) -> str:
    """把用户写的会议类型规范成模板名；识别不了返回空串。"""
    n = (name or "").strip()
    if not n:
        return ""
    tpl = load_templates()
    if n in tpl:
        return n
    alias = TEMPLATE_ALIASES.get(n)
    if alias and alias in tpl:
        return alias
    for k in tpl:
        if k.rstrip("会") == n.rstrip("会"):
            return k
    return ""


def scan_models(roots, log=None) -> list:
    """扫描给定根目录，识别可用模型，返回登记项（只记路径，不复制）。"""
    found, seen = [], set()
    for root in roots:
        rp = Path(root).expanduser()
        if not rp.exists():
            continue
        for d in [rp] + [p for p in rp.rglob("*") if p.is_dir()]:
            try:
                if all((d / f).exists() for f in ASR_SIGNATURE):
                    key = str(d.resolve()).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    size_name = d.name
                    for s in ("tiny", "base", "small", "medium", "large"):
                        if s in d.name.lower():
                            size_name = s
                            break
                    found.append({
                        "id": f"asr-{size_name}",
                        "kind": "faster-whisper",
                        "size": size_name,
                        "path": str(d),
                        "bytes": dir_size(d),
                        "source": "local-scan",
                    })
            except OSError:
                continue
        for f in rp.rglob("*.onnx"):
            key = str(f.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                found.append({
                    "id": f.stem,
                    "kind": "onnx",
                    "size": "",
                    "path": str(f),
                    "bytes": f.stat().st_size,
                    "source": "local-scan",
                })
            except OSError:
                continue
        if log:
            log(f"[扫描] {rp}")
    return found


def build_registry(home, models_dir, extra_roots=None) -> dict:
    roots = [str(models_dir)]
    roots += [str(r) for r in (extra_roots or [])]
    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "home": str(home),
        "roots": roots,
        "models": scan_models(roots),
    }
