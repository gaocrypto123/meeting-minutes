#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meeting-minutes v2 · Phase 1 本地离线转写（含说话人分离）

产出两份，分工明确：
    --out-md    人类可读转写稿：`[00:12:30] 发言人2：……`
    --out-json  机器可读：每段起止秒 / 发言人 / 置信度，供“出处锚点校验”用

设计要点：
    * 模型、语言、分块等默认值全部来自 config.json，换机器只改配置
    * --start / --duration 支持只跑中间某几分钟（试跑音质用）
    * 分块之间留 --overlap 秒重叠，减少跨块丢上下文
    * 自动归一化音量、自动剔除提示词泄漏与机械重复幻觉，并把缺口写成区间
    * 全程本地，音频不出本机
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import PKG_ROOT, Log, decode_audio, hms, load_config  # noqa: E402

LEAK_PATTERNS = ("请输出简体中文", "请输出简体", "字幕由", "以下是", "请识别", "Transcribed by")


def load_glossary(path) -> list:
    """读术语纠错表：每行「错的写法 = 正确写法」，# 为注释。"""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    rules = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        wrong, right = (x.strip() for x in line.split("=", 1))
        if wrong and right and wrong != right:
            rules.append((wrong, right))
    rules.sort(key=lambda r: -len(r[0]))       # 长规则优先，避免被短规则打断
    return rules


def apply_glossary(text: str, rules: list):
    """确定性替换，返回 (新文本, 替换次数)。英文不分大小写。"""
    import re

    hits = 0
    for wrong, right in rules:
        if wrong.isascii():
            pat = re.compile(re.escape(wrong), re.IGNORECASE)
            text, n = pat.subn(right, text)
        else:
            n = text.count(wrong)
            if n:
                text = text.replace(wrong, right)
        hits += n
    return text, hits


def is_hallucination(text: str, min_run: int = 5, min_cover: float = 0.6) -> bool:
    """检测机械重复幻觉。

    旧版只能识别“整句恰好是同一片段的整数倍”，对下面两类完全无效：
        「这个应该是这个应该是这个应该是…」（带前缀）
        「一瓶一瓶啊?一瓶啊?一瓶啊?一瓶啊?…」（带前缀且周期不整齐）
    新版改为：在句子里找“同一小片段连续重复 ≥ min_run 次”的最长串，
    若它覆盖了整句 ≥ min_cover，则判为幻觉。
    """
    t = "".join(text.split())
    n = len(t)
    if n < 12:                       # 太短不动，避免误伤“对对对对”
        return False
    best = 0
    for p in range(2, min(13, n // min_run + 1)):
        i = 0
        while i + p <= n:
            unit = t[i:i + p]
            k, j = 1, i + p
            while j + p <= n and t[j:j + p] == unit:
                k += 1
                j += p
            if k >= min_run:
                best = max(best, k * p)
                i = j
            else:
                i += 1
    return best >= min_cover * n


def assign_speakers(segments: list, diar: list) -> None:
    """按时间重叠把说话人标签贴到 ASR 段上（就地修改）。"""
    for seg in segments:
        best, best_ov, best_i = None, 0.0, -1
        for i, d in enumerate(diar):
            ov = min(seg["end"], d["end"]) - max(seg["start"], d["start"])
            if ov > best_ov:
                best, best_ov, best_i = d["speaker"], ov, i
        seg["speaker_raw"] = best
        seg["diar_idx"] = best_i          # 说话人切换点：同一段内才合并
    order = []
    for seg in segments:
        s = seg.get("speaker_raw")
        if s is not None and s not in order:
            order.append(s)
    label = {s: f"发言人{i + 1}" for i, s in enumerate(order)}
    for seg in segments:
        seg["speaker"] = label.get(seg.get("speaker_raw"), "未知")


def find_gaps(segments: list, duration: float, min_gap: float = 20.0) -> list:
    """找出长时间没有任何文字的时间区间（转写缺口）。"""
    gaps = []
    cursor = 0.0
    for seg in sorted(segments, key=lambda s: s["start"]):
        if seg["start"] - cursor >= min_gap:
            gaps.append({"start": round(cursor, 2), "end": round(seg["start"], 2),
                         "reason": "该区间无转写内容（静音或未识别）"})
        cursor = max(cursor, seg["end"])
    if duration - cursor >= min_gap:
        gaps.append({"start": round(cursor, 2), "end": round(duration, 2),
                     "reason": "该区间无转写内容（静音或未识别）"})
    return gaps


def merge_turns(segments: list, max_gap: float = 1.2, max_chars: int = 220) -> list:
    """把同一个发言人连续的短句合并成一段，让转写稿可读、也让下游更省 token。
    只在“同一发言人 + 间隔很小”时合并，避免把不同人的话串起来。"""
    turns = []
    for seg in segments:
        if turns:
            prev = turns[-1]
            if (prev.get("diar_idx") == seg.get("diar_idx")
                    and prev["speaker"] == seg["speaker"]
                    and seg["start"] - prev["end"] <= max_gap
                    and len(prev["text"]) < max_chars):
                prev["text"] = (prev["text"] + seg["text"]).strip()
                prev["end"] = seg["end"]
                prev["n"] += 1
                continue
        turns.append({"start": seg["start"], "end": seg["end"],
                      "speaker": seg["speaker"], "text": seg["text"], "n": 1,
                      "diar_idx": seg.get("diar_idx", -1)})
    return turns


def normalize_chunk(seg, target: float = 0.95, max_gain: float = 8.0):
    """按块归一化音量。

    整段录音按"全局峰值"归一化是有害的：会议开头有人大声说话时峰值很高，
    后面小声讨论的部分就被压得很低，模型在那一段容易听不清甚至编内容。
    改成每块单独归一化，块与块之间的音量就拉平了。
    """
    peak = float(abs(seg).max()) if len(seg) else 0.0
    if peak <= 1e-6:
        return seg, 1.0
    gain = min(target / peak, max_gain)
    return (seg * gain).astype("float32"), gain


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="meeting-minutes v2 本地离线转写")
    ap.add_argument("audio", help="音频/视频文件路径")
    ap.add_argument("--out-md", required=True, help="转写稿（Markdown）输出路径")
    ap.add_argument("--out-json", default="", help="机器可读 JSON 输出路径")
    ap.add_argument("--start", type=float, default=0.0, help="从第几秒开始（试跑用）")
    ap.add_argument("--duration", type=float, default=0.0, help="只处理多少秒")
    ap.add_argument("--limit", type=float, default=0.0, help="同 --duration（兼容旧写法）")
    ap.add_argument("--model", default="", help="small / medium（默认读配置）")
    ap.add_argument("--model-dir", default="", help="模型目录，默认 <models>/faster-whisper-<模型>")
    ap.add_argument("--language", default="")
    ap.add_argument("--chunk", type=float, default=0.0)
    ap.add_argument("--overlap", type=float, default=-1.0)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--no-vad", action="store_true")
    ap.add_argument("--prompt", default=None,
                    help="一般不要传：提示词会被当成台词吐出来")
    ap.add_argument("--keep-repeat", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--diarize", dest="diarize", action="store_true", default=None)
    ap.add_argument("--no-diarize", dest="diarize", action="store_false")
    ap.add_argument("--speakers", type=int, default=-1, help="已知人数就填；不填自动判断")
    ap.add_argument("--threshold", type=float, default=-1.0,
                    help="声纹聚类阈值（越大越合并）。不填则读配置")
    ap.add_argument("--glossary", default="", help="术语纠错表路径，默认读配置")
    ap.add_argument("--no-glossary", action="store_true", help="本次不做术语纠错")
    ap.add_argument("--config", default="")
    args = ap.parse_args()

    cfg = load_config(args.config or None)
    d = cfg["defaults"]
    model_size = args.model or d["asr_model"]
    models_dir = Path(cfg["models_dir"])
    model_dir = (Path(args.model_dir) if args.model_dir
                 else models_dir / f"faster-whisper-{model_size}")
    language = args.language or d["language"]
    chunk = args.chunk or float(d["chunk_seconds"])
    overlap = float(d["chunk_overlap_seconds"]) if args.overlap < 0 else args.overlap
    threads = args.threads or int(d["threads"])
    use_vad = (not args.no_vad) and bool(d["vad"])
    duration = args.duration or args.limit
    want_diar = cfg["diarization"]["enabled"] if args.diarize is None else args.diarize
    diar_threshold = (args.threshold if args.threshold >= 0
                      else float(cfg["diarization"].get("threshold", 1.0)))
    speakers = args.speakers
    if speakers < 0:
        speakers = int(cfg["diarization"].get("speakers", 0) or 0)
    glossary_path = ""
    glossary_local_path = ""
    if not args.no_glossary:
        g = args.glossary or cfg.get("glossary", "")
        if g:
            gp = Path(g)
            glossary_path = str(gp if gp.is_absolute() else PKG_ROOT / gp)
        gl = cfg.get("glossary_local", "术语表.local.txt")
        if gl:
            lp = Path(gl)
            glossary_local_path = str(lp if lp.is_absolute() else PKG_ROOT / lp)
    rules = load_glossary(glossary_path) + load_glossary(glossary_local_path)
    seen_rules, dedup = set(), []
    for w, r in rules:
        if w not in seen_rules:
            seen_rules.add(w)
            dedup.append((w, r))
    rules = sorted(dedup, key=lambda r: -len(r[0]))

    src = Path(args.audio)
    if not src.exists():
        print(f"[错误] 找不到音频：{src}", file=sys.stderr)
        return 2
    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    log = Log(out_md.with_suffix(out_md.suffix + ".log.txt"))

    log(f"[配置] 配置={cfg.get('_config_path')}  模型={model_size}  语言={language}"
        f"  分块={chunk:.0f}s 重叠={overlap:.0f}s 线程={threads} 说话人分离={want_diar}")
    if rules:
        extra = "（含本机私有词条）" if glossary_local_path and Path(glossary_local_path).exists() else ""
        log(f"[术语表] 载入规则 {len(rules)} 条{extra}")
    else:
        log("[术语表] 未启用")
    if not model_dir.exists():
        print(f"[错误] 找不到模型目录：{model_dir}\n"
              f"       先跑 bootstrap.py --install --models {model_size}", file=sys.stderr)
        return 2

    audio, meta = decode_audio(src, args.start, duration, log=log)
    dur = meta["duration"]

    from faster_whisper import WhisperModel
    log(f"[加载模型] {model_dir.name}  int8 / CPU / {threads} 线程")
    t0 = time.time()
    model = WhisperModel(str(model_dir), device="cpu", compute_type="int8",
                         cpu_threads=threads)
    log(f"  用时 {time.time() - t0:.0f}s")

    prog_file = out_md.with_suffix(out_md.suffix + f".{model_size}.progress.json")
    done = {}
    if args.resume and not duration and prog_file.exists():
        try:
            done = json.loads(prog_file.read_text(encoding="utf-8"))
        except Exception:
            done = {}

    n = max(1, int((dur + chunk - 1) // chunk))
    # 实测：本机 medium 约 2.3 倍实时、small 约 6 倍实时。给一个偏保守的区间。
    ratio = 2.3 if model_size == "medium" else 5.0
    lo, hi = dur / 60 / (ratio * 1.4), dur / 60 / (ratio * 0.6)
    eta = ("不到 1 分钟" if hi < 1 else
           f"{max(1, lo):.0f}-{hi:.0f} 分钟")
    log(f"[转写] {n} 块 × {chunk:.0f}s，预计 {eta}（CPU，"
        f"实测 {model_size} 约 {ratio:.1f} 倍实时）")
    t_all = time.time()
    for i in range(n):
        key = str(i)
        if key in done and done[key]:
            log(f"  块 {i + 1}/{n} 已有结果，跳过")
            continue
        s0 = i * chunk
        s1 = min(dur, s0 + chunk)
        seg = audio[int(s0 * 16000): int(s1 * 16000)]
        if len(seg) < 1600:
            done[key] = []
            continue
        seg, gain = normalize_chunk(seg)
        t1 = time.time()
        segments, _info = model.transcribe(
            seg, language=language, beam_size=5,
            vad_filter=use_vad,
            vad_parameters=dict(min_silence_duration_ms=350, speech_pad_ms=200),
            condition_on_previous_text=False,
            temperature=[0.0, 0.2, 0.4],
            initial_prompt=args.prompt,
        )
        rows = []
        for s in segments:
            txt = (s.text or "").strip()
            if txt:
                rows.append({
                    "start": round(s0 + s.start, 2),
                    "end": round(s0 + s.end, 2),
                    "text": txt,
                    "avg_logprob": round(float(getattr(s, "avg_logprob", 0.0) or 0.0), 3),
                    "no_speech_prob": round(float(getattr(s, "no_speech_prob", 0.0) or 0.0), 3),
                })
        done[key] = rows
        if not duration:
            prog_file.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
        log(f"  块 {i + 1}/{n}（{hms(s0)}-{hms(s1)}）{len(rows)} 段，"
            f"增益 {gain:.1f}x，用时 {time.time() - t1:.0f}s")

    raw = []
    for i in range(n):
        raw += done.get(str(i), [])
    raw.sort(key=lambda r: r["start"])

    gloss_hits, gloss_detail = 0, []
    if rules:
        for wrong, right in rules:
            hit = 0
            for r in raw:
                new, n = apply_glossary(r["text"], [(wrong, right)])
                if n:
                    r["text"] = new
                    hit += n
            if hit:
                gloss_hits += hit
                gloss_detail.append(f"{wrong}→{right}({hit})")
        if gloss_hits:
            log(f"[术语纠错] 共替换 {gloss_hits} 处：" + "、".join(gloss_detail))

    segments, leaks, repeats = [], 0, 0
    removed = []          # 被剔除的区间，写进缺口表，避免"假装它不存在"
    for r in raw:
        if segments and r["text"] == segments[-1]["text"] and r["start"] <= segments[-1]["start"] + 0.6:
            continue
        if any(b in r["text"] for b in LEAK_PATTERNS):
            leaks += 1
            removed.append({"start": r["start"], "end": r["end"],
                            "reason": "提示词泄漏（已剔除）"})
            continue
        if not args.keep_repeat and is_hallucination(r["text"]):
            repeats += 1
            removed.append({"start": r["start"], "end": r["end"],
                            "reason": "机械重复幻觉（已剔除，不可信）"})
            continue
        segments.append(r)

    # 重叠区去重：后一块开头与上一块结尾时间重叠的内容丢弃
    kept = []
    for r in segments:
        if kept and r["start"] < kept[-1]["end"] - overlap * 0.5:
            continue
        kept.append(r)
    segments = kept

    diar = []
    if want_diar:
        seg_model = cfg["diarization"]["segmentation_model"]
        emb_model = cfg["diarization"]["embedding_model"]
        if seg_model and emb_model and Path(seg_model).exists() and Path(emb_model).exists():
            try:
                from diarize import diarize as run_diar
                diar = run_diar(audio, seg_model, emb_model, speakers,
                                diar_threshold, log=log)
            except Exception as e:
                log(f"[警告] 说话人分离失败（{type(e).__name__}: {e}），本次不标发言人")
        else:
            log("[警告] 未找到说话人分离模型，本次不标发言人（可跑 "
                "bootstrap.py --install --with-diarization 补装）")
    if diar:
        assign_speakers(segments, diar)
    else:
        for r in segments:
            r["speaker"] = "未知"

    low_conf = [r for r in segments if r["avg_logprob"] < -1.0]
    suspect = [r for r in segments if r["no_speech_prob"] > 0.85]
    gaps = find_gaps(segments, dur)
    turns = merge_turns(segments)
    speakers = sorted({t["speaker"] for t in turns if t["speaker"] != "未知"})
    unknown_n = sum(1 for t in turns if t["speaker"] == "未知")

    first = meta["slice_start"]
    lines = [
        "# 01 · 转写稿（机器转写，未经人工校对）",
        "",
        f"- 源文件：`{src.name}`",
        f"- 源时长：{hms(meta['full_duration'])}；本次处理：{hms(first)} 起，共 {hms(dur)}",
        f"- 转写引擎：faster-whisper **{model_size}**（int8 / CPU / 本地离线，音频不出本机）",
        f"- 说话人分离：{'已启用，编号按首次出现顺序排列' if diar else '未启用'}",
        f"- 音量：RMS={meta['rms']} peak={meta['peak']}（已归一化）",
        f"- 发言人：{'、'.join(speakers)}（共 {len(speakers)} 位；编号只是声纹聚类，"
        f"不等于真实身份）"
        + (f"；另有 {unknown_n} 段未能归属到任何发言人" if unknown_n else ""),
        f"- 质量提示：合并后 {len(turns)} 段发言；低置信 {len(low_conf)} 段；"
        f"疑似静音/噪声 {len(suspect)} 段；无内容缺口 {len(gaps)} 处；"
        f"已剔除提示词泄漏 {leaks} 条、机械重复 {repeats} 条；"
        f"术语纠错 {gloss_hits} 处",
        "- 说明：机器转写存在同音字与断句误差；人名、数字、日期听不清处不猜，"
        "一律列入待确认清单。发言人编号只是声纹聚类结果，不代表真实身份。",
        "",
        "---",
        "",
    ]
    for i, t in enumerate(turns, 1):
        lines.append(f"{i}. [{hms(t['start'])}] {t['speaker']}：{t['text']}")
    if low_conf:
        lines += ["", "## 低置信片段（需人工核对）", ""]
        for r in low_conf:
            lines.append(f"- [{hms(r['start'])}] {r['speaker']}：{r['text']}"
                         f"（置信度 {r['avg_logprob']}）")
    if suspect:
        lines += ["", "## 疑似静音/噪声段（可能不是有效发言）", ""]
        for r in suspect:
            lines.append(f"- [{hms(r['start'])}] {r['speaker']}：{r['text']}"
                         f"（非语音概率 {r['no_speech_prob']}）")
    holes = sorted(gaps + removed, key=lambda g: g["start"])
    if holes:
        lines += ["", "## 转写缺口与剔除区间（该区间内容不进纪要正文）", "",
                  "| 起 | 止 | 时长 | 原因 |", "|---|---|---|---|"]
        for g in holes:
            lines.append(f"| {hms(g['start'])} | {hms(g['end'])} | "
                         f"{hms(g['end'] - g['start'])} | {g['reason']} |")
    lines.append("")
    out_md.write_text("\n".join(lines), encoding="utf-8")

    if args.out_json:
        payload = {
            "source": src.name,
            "model": model_size,
            "language": language,
            "meta": meta,
            "diarization": {"enabled": bool(diar), "segments": diar},
            "gaps": gaps,
            "removed": removed,
            "low_confidence": [{"start": r["start"], "end": r["end"]} for r in low_conf],
            "suspect_non_speech": [{"start": r["start"], "end": r["end"]} for r in suspect],
            "speakers": speakers,
            "segments": segments,
            "turns": turns,
        }
        Path(args.out_json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"[完成] {out_md}  合并后 {len(turns)} 段发言（原始 {len(segments)} 句）；"
        f"发言人 {len(speakers)} 位；低置信 {len(low_conf)} 段、缺口 {len(gaps)} 处；"
        f"总用时 {time.time() - t_all:.0f}s")
    if duration:
        log("[提示] 这是试跑结果（--start/--duration）。确认可用后去掉这两个参数跑全量。")
    else:
        log(f"[提示] 进度文件 {prog_file.name} 已保留；中断后加 --resume 可续跑。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
