# meeting-minutes · 技能本体

这个目录是**可以整个拷走直接安装**的技能包，里面只有 AI 要读的东西。
给人看的文档统一放在上一级的 [`docs/`](../docs)。

```
SKILL.md                 主流程 —— AI 读这个
config.json              配置项（模型档位、路径、阈值）
术语表.txt               术语纠错词表，按你的行业改
requirements.txt         依赖清单
requirements-lock.txt    锁定版本
references/              三份规范，AI 按需读
  templates.md           章节模板（五类会议 + 采访访谈）
  evidence-and-qc.md     证据字段与质检规范
  output-formats.md      交付物格式规范
scripts/                 15 个脚本，速查表见 scripts/README.md
```

## 给人看的文档在哪

| 文档 | 位置 |
|---|---|
| 完整说明与常见问题 | [`docs/说明书.md`](../docs/说明书.md) |
| 安装步骤、没有网络怎么办 | [`docs/安装说明.md`](../docs/安装说明.md) |
| 单文件版（不想装技能？整份粘给 AI） | [`docs/单文件版-可直接粘贴给AI.md`](../docs/单文件版-可直接粘贴给AI.md) |

`单文件版` 由 `scripts/make_single_file.py` 自动生成，改源头（`SKILL.md` / `references/`）后重跑即可，不要手改。

## 分清两类文件

- **AI 读的**：`SKILL.md`、`config.json`、`术语表.txt`、`references/`、`scripts/` —— 这些必须在技能目录里
- **人读的**：说明书、安装说明、单文件版 —— 这些在 `docs/`，技能装好后不需要

打包分发时（`scripts/make_package.py`）会自动把 `docs/` 里的三份文档一起收进 zip，所以解压后照样能看到说明书。
