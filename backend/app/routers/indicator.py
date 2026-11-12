"""指标口径管理接口：维护业务指标，覆盖提交审批、发布指标与发布前消费方确认。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.indicator import IndicatorService

router = APIRouter(prefix="/api/indicator", tags=["指标口径管理"])

service = IndicatorService()

LIST_FIELDS = ["指标编号", "指标名称", "计算公式", "统计粒度", "时间范围", "筛选条件", "数据来源", "口径版本"]
STATUSES = ["草稿", "待审批", "已发布"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按指标编号或指标名称检索"),
    status: str | None = Query(default=None, description="草稿、待审批、已发布"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按指标编号与状态过滤指标口径管理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/summary", response_model=dict)
def summary() -> dict[str, int]:
    """指标口径看板统计：指标数按业务编号去重，版本单独计数。"""
    return service.summary()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出指标口径管理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "indicator", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条业务指标版本明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"业务指标 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/versions", response_model=dict)
def list_versions(entry_id: int) -> dict[str, Any]:
    """按业务指标编号返回全部版本：历史已发布版本按当时口径留档。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"业务指标 {entry_id} 不存在或已归档")
    versions = service.by_code(str(entry.get("指标编号")))
    current = service.current_version(str(entry.get("指标编号")))
    return {
        "指标编号": entry.get("指标编号"),
        "current_version_id": int(current["id"]) if current else None,
        "versions": versions,
    }


@router.get("/{entry_id}/publish-review", response_model=dict)
def publish_review(entry_id: int) -> dict[str, Any]:
    """发布前确认视图：拟发布版本、上一稳定版本、下游模板与确认结果。"""
    payload, message = service.publish_review(entry_id)
    if payload is None:
        raise HTTPException(status_code=400, detail=message)
    return payload


@router.get("/{entry_id}/confirmations", response_model=dict)
def list_confirmations(entry_id: int) -> dict[str, Any]:
    """列出该版本已收集到的消费方确认结果。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"业务指标 {entry_id} 不存在或已归档")
    return {"indicator_id": entry_id, "items": service.confirmations(entry_id)}


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条业务指标，缺字段时说明原因而不是静默丢弃。"""
    values = payload.resolved_values()
    entry, missing = service.create_entry(values)
    if missing:
        has_blank_field = any(
            not str(values.get(field) or "").strip()
            for field in ("指标编号", "指标名称", "计算公式")
            if field in missing
        )
        if has_blank_field:
            return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
        return ActionResult(ok=False, message=missing[0])
    return ActionResult(ok=True, message="业务指标已登记", entry=entry)


@router.post("/{entry_id}/revisions", response_model=ActionResult)
def revise_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改指标公式：已发布版本生成新的待审批版本，绝不覆盖留档。"""
    entry, message = service.revise_formula(entry_id, payload.resolved_values())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/confirmations", response_model=ActionResult)
def confirm_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """记录消费方确认结果；同一版本、同一模板重复提交只保留一条。"""
    record, message = service.confirm(entry_id, payload.resolved_values())
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条业务指标执行提交审批、发布指标；不允许的跳级或回退会被拦下。"""
    action = str(payload.resolved_values().get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, payload.resolved_values())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)

