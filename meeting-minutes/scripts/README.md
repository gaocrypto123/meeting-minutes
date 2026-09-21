# scripts/ · 脚本速查表

15 个脚本，按流水线分六组。**开会只用到前五组**，第六组是维护/分发用的。

下面所有命令都假设你在技能包根目录（`SKILL.md` 所在那一层）执行。

## 谁先谁后

```
doctor.py                    ① 换电脑 / 跑不动 / 报错，先跑这个
   ↓
init_meeting_workspace.py    ② 建档，生成开工确认单
   ↓
transcribe.py                ③ 拿稿（三条路选一条）
   ↓                          ↳ 转不了才用 make_manual_template.py
qc_check.py                  ④ 六项质检（只查不补）
verify_refs.py                  出处锚点对账
   ↓
build_outputs.py             ⑤ 出 Word / HTML
build_ppt.py                    出 PPT（按需）

append_checkpoint.py          每结束一个阶段都调一次，记录节点
```

---

## ① 体检 / 环境（出问题先跑）

| 脚本 | 什么时候用 | 典型命令 |
|---|---|---|
| `doctor.py` | 换电脑、装不上、跑不动、报错。输出逐项体检 + 路线结论 A/B/C，**退出码 3 = 本机不可用，立刻转人工供稿** | `python scripts/doctor.py`<br>`python scripts/doctor.py --json`（机器可读）<br>`python scripts/doctor.py --offline`（跳过联网检查） |
| `bootstrap.py` | 建虚拟环境、装依赖、下模型。幂等，可反复跑 | `python scripts/bootstrap.py --status`（只看现状）<br>`python scripts/bootstrap.py --install --models small`<br>`python scripts/bootstrap.py --with-diarization`（连说话人分离模型一起下） |

## ② 建档（Phase 0）

| 脚本 | 作用 | 典型命令 |
|---|---|---|
| `init_meeting_workspace.py` | 建工作目录（原文/过程/纪要三层）+ 开工确认单 | `python scripts/init_meeting_workspace.py "9月产品周会" --type 内部工作会 --date 2026-09-21` |

`--type` 可选：`内部工作会` `决策评审会` `跨部门对接会` `外部沟通会` `采访访谈`。

## ③ 拿稿（Phase 1，三条路）

| 脚本 | 哪条路 | 典型命令 |
|---|---|---|
| `transcribe.py` | 路 2：本地离线转写（含说话人分离、术语纠错） | `python scripts/transcribe.py 录音.m4a --out-md 原文/转写稿.md --out-json 原文/转写稿.json` |
| `diarize.py` | 单独调说话人分离（改人数/阈值时用） | `python scripts/diarize.py 录音.m4a --speakers 5` |
| `make_manual_template.py` | 路 3：转不了时的人工补录模板 | `python scripts/make_manual_template.py 录音.m4a --out 原文/人工补录.md --segment 180` |

`--out-json` 一定要留着，Phase 4 的锚点校验要靠它跟纪要原话对账。

## ④ 质检（Phase 4）

| 脚本 | 作用 | 典型命令 |
|---|---|---|
| `qc_check.py` | 六项质检。**只查不补，不代写内容** | `python scripts/qc_check.py 03-纪要初稿.md --transcript-json 原文/转写稿.json` |
| `verify_refs.py` | 出处锚点对账，验证引用是真话还是编的 | `python scripts/verify_refs.py 20260921-周会` |

## ⑤ 出稿（Phase 5）

| 脚本 | 作用 | 典型命令 |
|---|---|---|
| `build_outputs.py` | Markdown → HTML / Word（卡片式排版） | `python scripts/build_outputs.py 20260921-周会`<br>`python scripts/build_outputs.py <目录> --no-docx`（只出 HTML） |
| `build_ppt.py` | 出 PPT，按需 | `python scripts/build_ppt.py 20260921-周会` |

## 全程穿插

| 脚本 | 作用 | 典型命令 |
|---|---|---|
| `append_checkpoint.py` | 每个阶段结束追加一条节点记录（编号/阶段/产出/依据/状态/待确认/下一节点） | `python scripts/append_checkpoint.py <目录> --id CP1 --phase "Phase 1 转写" --status 完成 --output "转写稿.md 12.4 KB" --basis "录音.m4a"`<br>`python scripts/append_checkpoint.py <目录> --list`（只看已有节点） |

## ⑥ 维护 / 分发（不开会时用）

| 脚本 | 作用 | 典型命令 |
|---|---|---|
| `install_skill.py` | 装/同步到 AI 的技能目录 | `python scripts/install_skill.py`（只预览）<br>`python scripts/install_skill.py --install`（真写入） |
| `make_single_file.py` | 把 SKILL.md + references 合成「单文件版」 | `python scripts/make_single_file.py` |
| `make_package.py` | 生成可发给别人的 zip（带敏感信息扫描） | `python scripts/make_package.py --name meeting-minutes-v3.0` |

`make_single_file.py` 的输出位置：仓库里统一写到 `docs/`，独立安装时就写在技能包根目录 —— 两种布局它都认。

## 内部依赖（不要直接跑）

| 脚本 | 作用 |
|---|---|
| `mm_common.py` | 公共库：配置读取、日志、音频解码、下载、模型库扫描 |

---

想不起来该跑哪个：先跑 `python scripts/doctor.py`，它会告诉你本机走哪条路。
