# 数据分析工作台

面向企业数据分析师与数据产品经理的一站式数据分析工作台。提供指标口径管理、聚合查询、分析实验设计、数据服务发布与应用集成的完整链路。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标口径管理 | `indicator` | 业务指标 | 指标编号、指标名称、计算公式 |
| 分析数据源 | `datasource` | 数据源连接 | 连接编号、连接名称、数据库类型 |
| 聚合查询配置 | `query` | 查询模板 | 模板编号、模板名称、查询语句 |
| 分析实验管理 | `experiment` | 分析实验 | 实验编号、实验名称、分析数据集 |
| 分析数据集 | `dataset` | 数据集 | 数据集编号、数据集名称、来源表名 |
| 可视化图表配置 | `chart` | 图表配置 | 图表编号、图表名称、图表类型 |
| 分析报告管理 | `report` | 分析报告 | 报告编号、报告标题、关联数据集 |
| 数据服务发布 | `service` | 数据服务 | 服务编号、服务名称、输出数据集 |
| 数据看板 | `dashboard` | 看板面板 | 面板编号、面板名称、布局配置 |
| 分析维度管理 | `dimension` | 分析维度 | 维度编号、维度名称、维度表名 |
| 全局筛选器 | `filter` | 筛选器 | 筛选器编号、筛选器名称、关联字段 |
| 定时分析任务 | `schedule` | 定时任务 | 任务编号、任务名称、分析类型 |
| 数据导出配置 | `export` | 导出模板 | 模板编号、模板名称、数据源绑定 |
| 查询缓存管理 | `cache` | 缓存规则 | 规则编号、缓存目标、缓存时间 |
| 分析模型管理 | `model` | 分析模型 | 模型编号、模型名称、模型类型 |
| 分析操作日志 | `log` | 操作日志 | 日志编号、操作类型、操作对象 |

## 指标口径发布确认

指标口径管理与聚合查询配置之间有一条跨模块的发布闸门：

1. **状态单向推进**：业务指标只能按 `草稿 → 待审批 → 已发布` 前进，跳级与回退都会被
   `services/indicator.py` 拦下并返回可读原因。
2. **口径版本化**：同一个指标编号（如 `INDI-0001`）对应多条版本记录。已发布版本按当时
   公式留档，不会被覆盖；对已发布版本修改公式会生成新的待审批版本，并继承指标编号。
3. **发布前确认**：`GET /api/indicator/{id}/publish-review` 汇总拟发布版本、上一稳定版本、
   引用该口径的查询模板及各自最后一次确认时间。
   `POST /api/indicator/{id}/confirmations` 记录消费方确认（`query_id` + `decision`），
   同一版本同一模板重复提交只更新一条结果。
4. **确认缺失不放行**：只要还有消费方未确认或拒绝，`POST /api/indicator/{id}/actions`
   的 `发布指标` 就返回失败并列出缺口，状态保持不变。
5. **发布后同步下游**：发布成功会把引用该口径的查询模板的 `引用指标版本`、`消费状态`
   更新为 `已切换`，确认时间直接取自同一条确认记录（与指标版本同源）。之后若再改公式，
   这些模板立即显示 `待重新确认`，且不能发布自身模板。

示例数据里的 `月度活跃客户`（INDI-0001）演示了完整链路：v1 已发布，v2 待审批，两个下游
模板 `QUER-0001` / `QUER-0002` 引用该口径，其中只有一个完成确认，发布会被拦截。
回归测试：`cd backend && .venv/bin/python -m unittest discover -s tests -v`。

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.tsx`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 动作接口同时接受扁平字段（`{"action": "提交审批"}`）与包裹写法
  （`{"values": {...}, "remark": "..."}`）。

领域：数据工程
子方向：数据分析与数据应用
