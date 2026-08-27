"""验证最终 legacy-audit 交接记录不会再次污染父仓库。"""

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class LegacyAuditHandoffTests(unittest.TestCase):
    """锁定本次基线集成的可恢复位置与任务状态。"""

    def test_handoff_records_external_audit_worktree(self) -> None:
        handoff = (PROJECT_ROOT / "handoff.md").read_text(encoding="utf-8")

        self.assertIn(
            "task_id: `pdf-diff-reader-segmentation-integration-v2-20260828`",
            handoff,
        )
        self.assertIn(
            "recorded_commit: `e8f7b198e2ab107e354a38efb21f746b0aa4ce75`",
            handoff,
        )
        self.assertIn("status: ready", handoff)
        self.assertIn(
            "/Users/mac/PycharmProjects/pdf-protocol-diff-worktrees/"
            "pdf-diff-reader-segmentation-integration",
            handoff,
        )


if __name__ == "__main__":
    unittest.main()
