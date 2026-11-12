"""指标口径管理业务规则：版本化、状态流转、消费方确认与发布闸门都收在这里。

一个业务指标编号（如 INDI-0001）在库里对应多条“口径版本”记录：
- 已发布的历史版本原样留档，公式不会被后续修改覆盖；
- 每次口径变化都会新建一条版本记录，从草稿推进到待审批；
- 发布前必须汇总所有引用该口径的查询模板，并逐一确认。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "indicator"
QUERY_MODULE = "query"
CONFIRMATION_MODULE = "_indicator_confirmation"
REQUIRED_FIELDS = ["指标编号", "指标名称", "计算公式"]

# 状态只允许单向推进：草稿 -> 待审批 -> 已发布，不允许跳级或回退。
STATUS_ORDER = ["草稿", "待审批", "已发布"]
ACTION_RULES: dict[str, tuple[str, str]] = {
    "提交审批": ("草稿", "待审批"),
    "发布指标": ("待审批", "已发布"),
}
NEGATIVE_ACTIONS: list[str] = []
CONFIRM_DECISIONS = ["确认", "拒绝"]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


class IndicatorService:
    # ------------------------------------------------------------------ 查询
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [
                row
                for row in rows
                if keyword in str(row.get("指标编号", ""))
                or keyword in str(row.get("指标名称", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def summary(self) -> dict[str, int]:
        """指标口径看板：按业务指标编号去重，另统计待审批与留档版本。"""
        rows = store.rows(MODULE)
        codes = {str(row.get("指标编号")) for row in rows}
        published_codes = {
            str(row.get("指标编号")) for row in rows if row.get("status") == "已发布"
        }
        pending = [row for row in rows if row.get("status") == "待审批"]
        archived = [
            row
            for row in rows
            if row.get("status") == "已发布" and not row.get("is_current")
        ]
        return {
            "indicator_count": len(codes),
            "published_count": len(published_codes),
            "pending_versions": len(pending),
            "archived_versions": len(archived),
        }

    def by_code(self, indicator_code: str) -> list[dict[str, Any]]:
        """按业务指标编号返回全部版本记录，按版本序号升序。"""
        rows = [
            row
            for row in store.rows(MODULE)
            if str(row.get("指标编号")) == indicator_code
        ]
        return sorted(rows, key=lambda row: int(row.get("version_seq", 0)))

    def current_version(self, indicator_code: str) -> dict[str, Any] | None:
        versions = self.by_code(indicator_code)
        for row in versions:
            if row.get("is_current"):
                return row
        return versions[-1] if versions else None

    # ------------------------------------------------------------------ 登记
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        code = str(values["指标编号"]).strip()
        if self.by_code(code):
            return None, [f"指标编号 {code} 已存在，请使用「变更口径」生成新版本"]
        rows = store.rows(MODULE)
        entry = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "status": STATUS_ORDER[0],
            "pending": True,
            "abnormal": False,
            "指标编号": code,
            "指标名称": values.get("指标名称"),
            "计算公式": values.get("计算公式"),
            "统计粒度": values.get("统计粒度"),
            "时间范围": values.get("时间范围"),
            "筛选条件": values.get("筛选条件"),
            "数据来源": values.get("数据来源"),
            "口径版本": "v1",
            "version_seq": 1,
            "previous_version_id": None,
            "is_current": True,
            "created_at": _now(),
            "published_at": None,
            "transitions": [
                {
                    "from": None,
                    "to": STATUS_ORDER[0],
                    "action": "登记指标",
                    "at": _now(),
                    "operator": "指标管理员",
                }
            ],
        }
        rows.append(entry)
        return entry, []

    # ------------------------------------------------------- 口径变更 / 状态
    def revise_formula(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        """修改指标公式。

        - 草稿版本：允许就地修订，不改变状态；
        - 已发布版本：绝不覆盖留档，生成一条新的待审批版本（保留指标编号）。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"业务指标 {entry_id} 不存在或已归档"
        formula = str(values.get("计算公式") or "").strip()
        if not formula:
            return None, "修改口径时必须填写新的计算公式"
        if entry.get("status") == "草稿":
            entry["计算公式"] = formula
            entry["pending"] = True
            return entry, "草稿版本公式已更新，可提交审批"
        if entry.get("status") != "已发布":
            return None, "只有草稿或已发布版本可以修改公式，待审批版本请等待审批结果"
        if not entry.get("is_current"):
            return None, "该版本已被后续版本取代，请对当前版本执行变更口径"
        return self._new_pending_version(entry, formula, action="变更口径")

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"业务指标 {entry_id} 不存在或已归档"
        values = values or {}
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于指标口径管理可执行范围"
        source_status, target_status = ACTION_RULES[action]
        current_status = entry.get("status")
        if current_status != source_status:
            return None, (
                f"指标状态必须按草稿、待审批、已发布依次推进："
                f"当前为「{current_status}」，不能执行「{action}」"
            )

        if action == "提交审批":
            entry["status"] = target_status
            entry["pending"] = True
            entry["abnormal"] = False
            self._append_transition(entry, current_status, target_status, action)
            # 新版本进入待审批后，下游查询模板必须明确显示待重新确认。
            self._refresh_consumer_states(entry)
            return entry, "业务指标已提交审批"

        # 发布指标：先做确认完整性校验，缺一条都不放行。
        blockers = self._publish_blockers(entry)
        if blockers:
            return None, "发布被拦截：" + "；".join(blockers)
        return self._publish(entry)

    # ------------------------------------------------------------- 发布确认
    def publish_review(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """发布前确认视图：拟发布版本、上一稳定版本、下游模板与确认结果。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"业务指标 {entry_id} 不存在或已归档"
        if entry.get("status") != "待审批":
            return None, f"只有待审批版本需要发布确认，当前状态为「{entry.get('status')}」"
        return self._review_payload(entry), ""

    def confirm(
        self,
        entry_id: int,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        """记录消费方对拟发布版本的确认结果；同一（版本，模板）只保留一条。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"业务指标 {entry_id} 不存在或已归档"
        if entry.get("status") != "待审批":
            return None, "只有待审批版本可以接收消费方确认"
        try:
            query_id = int(values.get("query_id"))
        except (TypeError, ValueError):
            return None, "确认时必须提供 query_id（引用该口径的查询模板编号）"
        decision = str(values.get("decision") or "").strip()
        if decision not in CONFIRM_DECISIONS:
            return None, "确认结果只能是「确认」或「拒绝」"
        query = store.find(QUERY_MODULE, query_id)
        if query is None:
            return None, f"查询模板 {query_id} 不存在或已归档"
        if query.get("status") == "已停用":
            return None, f"查询模板 {query.get('模板编号')} 已停用，不再需要消费方确认"
        if str(query.get("引用指标编号")) != str(entry.get("指标编号")):
            return None, (
                f"查询模板 {query.get('模板编号')} 未引用指标 {entry.get('指标编号')}，"
                "不能对该版本做确认"
            )

        rows = store.rows(CONFIRMATION_MODULE)
        existing = next(
            (
                row
                for row in rows
                if int(row.get("indicator_version_id", 0)) == entry_id
                and int(row.get("query_id", 0)) == query_id
            ),
            None,
        )
        now = _now()
        confirmed_by = str(values.get("confirmed_by") or "数据分析师").strip() or "数据分析师"
        if existing is not None:
            # 幂等刷新：只更新同一条结果，不追加新记录。
            existing["decision"] = decision
            existing["status"] = "已确认" if decision == "确认" else "已拒绝"
            existing["confirmed_by"] = confirmed_by
            existing["confirmed_at"] = now
            existing["updated_at"] = now
            if values.get("comment") is not None:
                existing["comment"] = str(values.get("comment"))
            record = existing
        else:
            record = {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "indicator_version_id": entry_id,
                "指标编号": entry.get("指标编号"),
                "query_id": query_id,
                "模板编号": query.get("模板编号"),
                "decision": decision,
                "status": "已确认" if decision == "确认" else "已拒绝",
                "confirmed_by": confirmed_by,
                "confirmed_at": now,
                "comment": str(values.get("comment") or ""),
                "created_at": now,
                "updated_at": now,
            }
            rows.append(record)

        # 同步下游模板上的确认时间与确认状态，保证清单里能直接看到。
        query["指标确认状态"] = "已确认" if decision == "确认" else "待重新确认"
        query["指标最后确认时间"] = now
        query["指标确认人"] = confirmed_by
        return record, f"查询模板 {query.get('模板编号')} 已{decision}该口径版本"

    def confirmations(self, entry_id: int) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in store.rows(CONFIRMATION_MODULE)
            if int(row.get("indicator_version_id", 0)) == entry_id
        ]

    # ---------------------------------------------------------------- 内部
    def _append_transition(
        self, entry: dict[str, Any], from_status: str | None, to_status: str, action: str
    ) -> None:
        entry.setdefault("transitions", []).append(
            {
                "from": from_status,
                "to": to_status,
                "action": action,
                "at": _now(),
                "operator": "指标管理员",
            }
        )

    def _new_pending_version(
        self, published: dict[str, Any], formula: str, *, action: str
    ) -> tuple[dict[str, Any] | None, str]:
        """为已发布指标生成一条新的待审批版本，历史版本原样留档。"""
        code = str(published.get("指标编号"))
        versions = self.by_code(code)
        seq = max(int(row.get("version_seq", 0)) for row in versions) + 1
        latest_published = max(
            (row for row in versions if row.get("status") == "已发布"),
            key=lambda row: int(row.get("version_seq", 0)),
            default=None,
        )
        rows = store.rows(MODULE)
        now = _now()
        entry = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "status": STATUS_ORDER[0],
            "pending": True,
            "abnormal": False,
            "指标编号": code,
            "指标名称": published.get("指标名称"),
            "计算公式": formula,
            "统计粒度": published.get("统计粒度"),
            "时间范围": published.get("时间范围"),
            "筛选条件": published.get("筛选条件"),
            "数据来源": published.get("数据来源"),
            "口径版本": f"v{seq}",
            "version_seq": seq,
            "previous_version_id": int(latest_published["id"]) if latest_published else None,
            "is_current": True,
            "created_at": now,
            "published_at": None,
            "transitions": [
                {
                    "from": None,
                    "to": STATUS_ORDER[0],
                    "action": action,
                    "at": now,
                    "operator": "指标管理员",
                }
            ],
        }
        rows.append(entry)
        # 变更口径必须生成待审批版本：草稿 -> 待审批 一次状态切换，记录两段流转。
        self._append_transition(entry, STATUS_ORDER[0], "待审批", "提交审批")
        entry["status"] = "待审批"
        entry["pending"] = True
        # 当前版本指针切到新版本，旧发布版本降级为历史留档（公式不变）。
        for row in versions:
            row["is_current"] = False
        self._refresh_consumer_states(entry)
        return entry, (
            f"已为指标 {code} 生成新的待审批版本 {entry['口径版本']}，"
            "原已发布版本按当时口径留档"
        )

    def _refresh_consumer_states(self, version: dict[str, Any]) -> None:
        """指标口径变化后，让下游查询模板明确显示待重新确认。"""
        code = str(version.get("指标编号"))
        version_id = int(version["id"])
        for query in store.rows(QUERY_MODULE):
            if str(query.get("引用指标编号")) != code:
                continue
            if query.get("status") == "已停用":
                continue
            confirmed = self._confirmation_for(version_id, int(query["id"]))
            if confirmed is not None and confirmed.get("decision") == "确认":
                query["指标确认状态"] = "已确认"
            else:
                query["指标确认状态"] = "待重新确认"
            if int(query.get("引用指标版本ID", 0)) != version_id:
                query["消费状态"] = "待切换"

    def _confirmation_for(
        self, version_id: int, query_id: int
    ) -> dict[str, Any] | None:
        for row in store.rows(CONFIRMATION_MODULE):
            if (
                int(row.get("indicator_version_id", 0)) == version_id
                and int(row.get("query_id", 0)) == query_id
            ):
                return row
        return None

    def _consumers(self, version: dict[str, Any]) -> list[dict[str, Any]]:
        code = str(version.get("指标编号"))
        return [
            query
            for query in store.rows(QUERY_MODULE)
            if str(query.get("引用指标编号")) == code and query.get("status") != "已停用"
        ]

    def _last_confirmation(self, query: dict[str, Any]) -> dict[str, Any] | None:
        """该模板针对当前指标编号最近一次的确认（不限版本）。"""
        code = str(query.get("引用指标编号"))
        rows = [
            row
            for row in store.rows(CONFIRMATION_MODULE)
            if int(row.get("query_id", 0)) == int(query.get("id", 0))
            and str(row.get("指标编号")) == code
        ]
        return max(rows, key=lambda row: str(row.get("updated_at", "")), default=None)

    def _previous_stable(self, version: dict[str, Any]) -> dict[str, Any] | None:
        published = [
            row
            for row in self.by_code(str(version.get("指标编号")))
            if row.get("status") == "已发布"
            and int(row.get("version_seq", 0)) < int(version.get("version_seq", 0))
        ]
        return max(published, key=lambda row: int(row.get("version_seq", 0)), default=None)

    def _review_payload(self, version: dict[str, Any]) -> dict[str, Any]:
        previous = self._previous_stable(version)
        consumers: list[dict[str, Any]] = []
        blockers: list[str] = []
        version_id = int(version["id"])
        for query in self._consumers(version):
            confirmation = self._confirmation_for(version_id, int(query["id"]))
            confirmed = confirmation is not None and confirmation.get("decision") == "确认"
            last = self._last_confirmation(query)
            consumers.append(
                {
                    "query_id": int(query["id"]),
                    "模板编号": query.get("模板编号"),
                    "模板名称": query.get("模板名称"),
                    "模板版本": query.get("模板版本"),
                    "引用指标版本": query.get("引用指标版本"),
                    "消费状态": query.get("消费状态"),
                    "指标确认状态": query.get("指标确认状态"),
                    "confirmed_for_proposed": confirmed,
                    "decision": confirmation.get("decision") if confirmation else None,
                    "last_confirmed_at": (
                        confirmation.get("confirmed_at")
                        if confirmation
                        else (last.get("confirmed_at") if last else None)
                    ),
                    "last_confirmed_by": (
                        confirmation.get("confirmed_by")
                        if confirmation
                        else (last.get("confirmed_by") if last else None)
                    ),
                }
            )
            if not confirmed:
                blockers.append(
                    f"查询模板 {query.get('模板编号')}（{query.get('模板名称')}）"
                    f"尚未确认拟发布版本 {version.get('口径版本')}"
                )
        return {
            "indicator_id": int(version["id"]),
            "指标编号": version.get("指标编号"),
            "指标名称": version.get("指标名称"),
            "proposed_version": self._version_brief(version),
            "previous_stable_version": self._version_brief(previous) if previous else None,
            "consumers": consumers,
            "required_confirmations": len(consumers),
            "confirmed_count": sum(1 for item in consumers if item["confirmed_for_proposed"]),
            "ready": not blockers,
            "blockers": blockers,
        }

    def _version_brief(self, version: dict[str, Any] | None) -> dict[str, Any] | None:
        if version is None:
            return None
        return {
            "id": int(version["id"]),
            "指标编号": version.get("指标编号"),
            "指标名称": version.get("指标名称"),
            "口径版本": version.get("口径版本"),
            "计算公式": version.get("计算公式"),
            "status": version.get("status"),
            "created_at": version.get("created_at"),
            "published_at": version.get("published_at"),
        }

    def _publish_blockers(self, version: dict[str, Any]) -> list[str]:
        """发布闸门：确认数据缺失时绝不静默通过。"""
        blockers: list[str] = []
        consumers = self._consumers(version)
        for query in consumers:
            confirmation = self._confirmation_for(int(version["id"]), int(query["id"]))
            if confirmation is None:
                blockers.append(
                    f"查询模板 {query.get('模板编号')}（{query.get('模板名称')}）缺少消费方确认结果"
                )
            elif confirmation.get("decision") != "确认":
                blockers.append(
                    f"查询模板 {query.get('模板编号')}（{query.get('模板名称')}）确认结果为"
                    f"「{confirmation.get('decision')}」，不能发布"
                )
        if not version.get("计算公式"):
            blockers.append("拟发布版本缺少计算公式")
        return blockers

    def _publish(self, version: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        now = _now()
        version["status"] = "已发布"
        version["pending"] = False
        version["abnormal"] = False
        version["published_at"] = now
        for row in self.by_code(str(version.get("指标编号"))):
            row["is_current"] = int(row["id"]) == int(version["id"])
        self._append_transition(version, "待审批", "已发布", "发布指标")

        # 跨模块同步下游查询模板：消费状态、引用版本与确认时间都取自同一条确认记录。
        for query in self._consumers(version):
            confirmation = self._confirmation_for(int(version["id"]), int(query["id"]))
            if confirmation is None:  # 闸门已保证不会发生，这里仅作防御
                continue
            query["引用指标版本"] = version.get("口径版本")
            query["引用指标版本ID"] = int(version["id"])
            query["指标确认状态"] = "已确认"
            query["指标最后确认时间"] = confirmation.get("confirmed_at")
            query["指标确认人"] = confirmation.get("confirmed_by")
            query["消费状态"] = "已切换"
        return version, (
            f"业务指标已发布，{len(self._consumers(version))} 个下游查询模板"
            "的消费状态已同步为已切换"
        )
