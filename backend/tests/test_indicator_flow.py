"""指标口径版本流转与发布前确认的回归测试。

运行：cd backend && .venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from app.services.indicator import CONFIRMATION_MODULE, IndicatorService
from app.services.query import QueryService
from app.store import store


class IndicatorPublishFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        self.indicator = IndicatorService()
        self.query = QueryService()
        self.pending = 2  # INDI-0001 v2（月度活跃客户）待审批版本
        self.published_v1 = 1

    def _confirm_all(self) -> None:
        self.indicator.confirm(self.pending, {"query_id": 1, "decision": "确认", "confirmed_by": "李雷"})
        self.indicator.confirm(self.pending, {"query_id": 2, "decision": "确认", "confirmed_by": "韩梅梅"})

    def test_review_shows_versions_and_both_consumers(self) -> None:
        review, message = self.indicator.publish_review(self.pending)
        self.assertIsNotNone(review, message)
        assert review is not None
        self.assertEqual(review["指标编号"], "INDI-0001")
        self.assertEqual(review["proposed_version"]["口径版本"], "v2")
        self.assertEqual(review["previous_stable_version"]["口径版本"], "v1")
        self.assertEqual(review["required_confirmations"], 2)
        templates = {item["模板编号"]: item for item in review["consumers"]}
        self.assertEqual(set(templates), {"QUER-0001", "QUER-0002"})
        # 每条确认记录都要带最后确认时间，哪怕还没确认拟发布版本。
        for item in templates.values():
            self.assertIsNotNone(item["last_confirmed_at"])

    def test_publish_blocked_without_full_confirmation(self) -> None:
        entry, message = self.indicator.run_action(self.pending, "发布指标")
        self.assertIsNone(entry)
        self.assertIn("QUER-0002", message)
        # 指标状态保持不变，没有静默通过。
        self.assertEqual(self.indicator.get_entry(self.pending)["status"], "待审批")
        self.assertEqual(self.query.get_entry(2)["消费状态"], "待切换")

    def test_publish_blocked_when_rejected(self) -> None:
        self._confirm_all()
        self.indicator.confirm(self.pending, {"query_id": 2, "decision": "拒绝"})
        entry, message = self.indicator.run_action(self.pending, "发布指标")
        self.assertIsNone(entry)
        self.assertIn("拒绝", message)

    def test_duplicate_confirmation_keeps_single_result(self) -> None:
        before = len(store.rows(CONFIRMATION_MODULE))
        for index in range(4):
            record, message = self.indicator.confirm(
                self.pending,
                {"query_id": 2, "decision": "确认", "comment": f"第 {index} 次刷新"},
            )
            self.assertIsNotNone(record, message)
        rows = self.indicator.confirmations(self.pending)
        matches = [row for row in rows if row["query_id"] == 2]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["comment"], "第 3 次刷新")
        self.assertEqual(len(store.rows(CONFIRMATION_MODULE)), before + 1)

    def test_publish_syncs_downstream_consumption(self) -> None:
        self._confirm_all()
        entry, message = self.indicator.run_action(self.pending, "发布指标")
        self.assertIsNotNone(entry, message)
        assert entry is not None
        self.assertEqual(entry["status"], "已发布")
        for query_id in (1, 2):
            template = self.query.get_entry(query_id)
            assert template is not None
            self.assertEqual(template["消费状态"], "已切换")
            self.assertEqual(template["指标确认状态"], "已确认")
            self.assertEqual(template["引用指标版本"], "v2")
            self.assertEqual(template["引用指标版本ID"], self.pending)
        # 确认时间与指标版本的同一条确认记录保持同源。
        record = next(
            row
            for row in self.indicator.confirmations(self.pending)
            if row["query_id"] == 2
        )
        self.assertEqual(self.query.get_entry(2)["指标最后确认时间"], record["confirmed_at"])

    def test_transitions_cannot_skip_or_roll_back(self) -> None:
        draft_id = 3
        # 草稿不能直接发布
        entry, message = self.indicator.run_action(draft_id, "发布指标")
        self.assertIsNone(entry)
        self.assertIn("依次推进", message)
        # 待审批不能再次提交审批
        entry, message = self.indicator.run_action(self.pending, "提交审批")
        self.assertIsNone(entry)
        # 已发布不能回退
        entry, message = self.indicator.run_action(self.published_v1, "提交审批")
        self.assertIsNone(entry)
        # 草稿 -> 待审批 是唯一合法前进一步
        entry, message = self.indicator.run_action(draft_id, "提交审批")
        self.assertIsNotNone(entry, message)
        assert entry is not None
        self.assertEqual(entry["status"], "待审批")

    def test_revision_creates_new_pending_version_and_keeps_archive(self) -> None:
        self._confirm_all()
        entry, message = self.indicator.run_action(self.pending, "发布指标")
        self.assertIsNotNone(entry, message)
        v2_formula = self.indicator.get_entry(self.pending)["计算公式"]

        new_entry, message = self.indicator.revise_formula(
            self.pending, {"计算公式": "COUNT(DISTINCT 客户编号) WHERE 当月有效交易金额 > 0 / 期初客户总数"}
        )
        self.assertIsNotNone(new_entry, message)
        assert new_entry is not None
        # 新版本沿用同一个业务指标编号，进入待审批而不是覆盖已发布版本。
        self.assertEqual(new_entry["指标编号"], "INDI-0001")
        self.assertEqual(new_entry["口径版本"], "v3")
        self.assertEqual(new_entry["status"], "待审批")
        self.assertEqual(new_entry["previous_version_id"], self.pending)
        # 历史已发布版本公式原样留档。
        self.assertEqual(self.indicator.get_entry(self.pending)["计算公式"], v2_formula)
        self.assertEqual(self.indicator.get_entry(self.pending)["status"], "已发布")
        self.assertFalse(self.indicator.get_entry(self.pending)["is_current"])
        # 新版本未确认前不能发布。
        blocked, message = self.indicator.run_action(int(new_entry["id"]), "发布指标")
        self.assertIsNone(blocked)
        # 下游模板明确显示待重新确认。
        for query_id in (1, 2):
            self.assertEqual(self.query.get_entry(query_id)["指标确认状态"], "待重新确认")
            self.assertEqual(self.query.get_entry(query_id)["消费状态"], "待切换")

    def test_versions_endpoint_data_returns_full_history(self) -> None:
        versions = self.indicator.by_code("INDI-0001")
        self.assertEqual([row["口径版本"] for row in versions], ["v1", "v2"])
        current = self.indicator.current_version("INDI-0001")
        assert current is not None
        self.assertEqual(current["status"], "待审批")

    def test_pending_query_cannot_publish_before_indicator_confirmed(self) -> None:
        # 下游模板在指标新口径确认前，自身也不允许发布。
        self.assertEqual(self.query.get_entry(2)["指标确认状态"], "待重新确认")
        entry, message = self.query.run_action(2, "发布模板")
        self.assertIsNone(entry)
        self.assertIn("尚未确认", message)
        # 指标发布后，确认状态同步为已确认，模板才恢复发布能力。
        self._confirm_all()
        self.indicator.run_action(self.pending, "发布指标")
        self.assertEqual(self.query.get_entry(2)["指标确认状态"], "已确认")
        entry, message = self.query.run_action(2, "发布模板")
        self.assertIsNotNone(entry, message)

    def test_summary_counts_versions_per_indicator(self) -> None:
        summary = self.indicator.summary()
        self.assertEqual(summary["indicator_count"], 3)
        self.assertEqual(summary["published_count"], 2)
        self.assertEqual(summary["pending_versions"], 1)


if __name__ == "__main__":
    unittest.main()
