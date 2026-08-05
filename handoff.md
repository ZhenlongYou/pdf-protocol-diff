# PDF Protocol Diff Handoff

- task_id: `split-pdf-protocol-diff-20260805`
- goal: 从 RinysProject 保留历史拆出独立 GitHub 仓库。
- source_repository: `ZhenlongYou/codex`
- source_path: `codex_projects/pdf_protocol_diff`
- source_tree: `7b9df50708e09b6a825f9e04d8cea06b7268a915`
- preserved_history_commit: `2edefcea9d74ed59ba7e95e3e8b10a46afa1ccf1`
- target_repository: `ZhenlongYou/pdf-protocol-diff`
- branch: `codex/split-pdf-protocol-diff-20260805`
- base_main: `98ae59d2b334b5204e16c444f2c52ddfd2451c48`
- recorded_commit: `012e2ee003c58285f25fbb585555aeaef1b648df`
- status: ready
- real_entrypoint: `python3 gui_app.py --smoke-test`

历史 split commit 的 tree 与父仓项目 tree 完全一致。GitHub fresh clone 已进入 canonical 路径，迁移前后 GUI smoke 与 805 项 unittest 均通过；`.venv` 和被忽略的本地数据已恢复；等待最终复审、合入与独立仓 gate。
