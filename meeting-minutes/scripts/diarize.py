#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meeting-minutes v2 · 说话人分离（sherpa-onnx，纯 CPU，本地离线）

输出“谁在什么时候说话”的时间段，编号为 speaker 0..N。
转写脚本会把它排成“发言人1..N”。

注意：它只给编号，不认识名字；编号在录音之间不通用。

用法：
    python diarize.py <音频> --start 360 --duration 180
    python diarize.py <音频> --speakers 5 --out diar.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import decode_audio, hms, load_config  # noqa: E402


def diarize(audio, seg_model: str, emb_model: str, num_speakers: int = -1,
            threshold: float = 0.5, log=print) -> list:
    """返回 [{start, end, speaker}]，单位为秒。"""
    import sherpa_onnx

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=seg_model),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=emb_model),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers, threshold=threshold),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    if abs(sd.sample_rate - 16000) > 1:
        raise RuntimeError(f"模型期望采样率 {sd.sample_rate}，本脚本固定用 16k 单声道")

    t0 = time.time()
    result = sd.process(audio)
    try:
        result = result.sort_by_start_time()
    except Exception:
        pass
    segs = [{"start": round(float(r.start), 2), "end": round(float(r.end), 2),
             "speaker": int(r.speaker)} for r in result]
    spk = sorted({s["speaker"] for s in segs})
    log(f"[说话人] {len(segs)} 段，声纹聚类 {len(spk)} 个（编号 {spk}），"
        f"用时 {time.time() - t0:.0f}s")
    return segs


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="说话人分离（本地离线）")
    ap.add_argument("audio")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--duration", type=float, default=0.0)
    ap.add_argument("--speakers", type=int, default=-1,
                    help="已知人数就填（如 5）；不填则自动判断")
    ap.add_argument("--threshold", type=float, default=-1.0,
                    help="声纹聚类阈值（越大越倾向于合并）。不填则读配置")
    ap.add_argument("--out", default="")
    ap.add_argument("--config", default="")
    args = ap.parse_args()

    cfg = load_config(args.config or None)
    seg_model = cfg["diarization"]["segmentation_model"]
    emb_model = cfg["diarization"]["embedding_model"]
    threshold = (args.threshold if args.threshold >= 0
                 else float(cfg["diarization"].get("threshold", 1.0)))
    for p in (seg_model, emb_model):
        if not p or not Path(p).exists():
            print(f"[错误] 找不到说话人分离模型：{p}\n"
                  f"       先跑 bootstrap.py --install --with-diarization",
                  file=sys.stderr)
            return 2

    src = Path(args.audio)
    if not src.exists():
        print(f"[错误] 找不到音频：{src}", file=sys.stderr)
        return 2

    audio, meta = decode_audio(src, args.start, args.duration)
    segs = diarize(audio, seg_model, emb_model, args.speakers, threshold)

    if args.out:
        Path(args.out).write_text(
            json.dumps({"source": src.name, "meta": meta, "segments": segs},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[完成] {args.out}")
    else:
        for s in segs:
            print(f"  {hms(s['start'])} - {hms(s['end'])}  声纹{s['speaker']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
