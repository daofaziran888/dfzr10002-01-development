"""聚合查询配置业务规则：状态流转、字段校验与筛选口径都收在这里。

查询模板可以引用业务指标口径；引用关系与消费方确认状态由指标服务在发布时同步，
本模块只负责读取与模板自身的发布/停用流转。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "query"
REQUIRED_FIELDS = ["模板编号", "模板名称", "查询语句"]
STATUS_ORDER = ["草稿", "待审批", "已发布", "已停用"]
ACTION_RULES = {"提交审批": "待审批", "发布模板": "已发布", "停用模板": "已停用"}
NEGATIVE_ACTIONS = ["停用模板"]


class QueryService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        indicator_code: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("模板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if indicator_code:
            rows = [
                row for row in rows if str(row.get("引用指标编号")) == indicator_code
            ]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["引用指标编号"] = values.get("引用指标编号")
        entry["引用指标版本"] = None
        entry["引用指标版本ID"] = None
        entry["指标确认状态"] = "待重新确认" if values.get("引用指标编号") else "未引用指标"
        entry["指标最后确认时间"] = None
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"查询模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于聚合查询配置可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        # 已发布的模板如果引用了尚未确认新口径的指标，不允许继续发布。
        if action == "发布模板" and entry.get("指标确认状态") == "待重新确认":
            return None, (
                f"查询模板 {entry.get('模板编号')} 引用的指标 {entry.get('引用指标编号')} "
                "口径变更尚未确认，请先完成确认再发布"
            )
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"查询模板已{action}"
