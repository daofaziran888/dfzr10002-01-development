"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

import copy
from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        # 深拷贝：版本记录、确认记录都是嵌套结构，浅拷贝会让多个 Store 实例
        # 共享同一份列表，测试或多次初始化时互相污染。
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: copy.deepcopy(rows) for name, rows in SEED_ROWS.items()
        }

    def module_names(self) -> list[str]:
        # 下划线开头的表是跨模块共用的内部记录（如消费方确认），不当作业务模块统计。
        return sorted(name for name in self._tables if not name.startswith("_"))

    def reset(self) -> None:
        """恢复示例数据：测试和本地演示需要从同一份初始状态开始。"""
        self._tables = {
            name: copy.deepcopy(rows) for name, rows in SEED_ROWS.items()
        }

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
