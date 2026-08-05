# PDF Protocol Diff Handoff

- task_id: `bootstrap-pdf-protocol-diff-20260805`
- goal: 从 RinysProject 保留历史拆出独立 GitHub 仓库。
- source_repository: `ZhenlongYou/codex`
- source_path: `codex_projects/pdf_protocol_diff`
- source_tree: `7b9df50708e09b6a825f9e04d8cea06b7268a915`
- preserved_history_commit: `2edefcea9d74ed59ba7e95e3e8b10a46afa1ccf1`
- target_repository: `ZhenlongYou/pdf-protocol-diff`
- status: bootstrap_ready
- real_entrypoint: `python3 gui_app.py --smoke-test`

历史 split commit 的 tree 与父仓项目 tree 完全一致。迁移前 GUI smoke 与 805 项 unittest 通过；下一步从 GitHub 重新 clone 到 canonical 路径并执行独立仓 gate。
