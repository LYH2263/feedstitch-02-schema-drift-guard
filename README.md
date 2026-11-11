# 01-feedstitch（信汇）

Feedstitch — 多源目录/价目 Feed 归并落库：解析 → 业务键去重/冲突隔离 → 按源水位线增量或全量落库。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5100 |
| API | http://localhost:10100 |

## 主链

配置源与夹具 → 触发 Run → 解析 / 去重 / 冲突隔离 → Canonical + 运行报告对账

## 种子说明

- **Alpha Catalog（clean）**：JSON，三件干净货号，契约匹配时增量可落库。
- **Beta Offers（dirty）**：JSONL，含与 Alpha 同键但价格不同的冲突行，以及迟到低 revision 行（底座增量会因水位线跳过）。
- **Gamma Shelf（xml）**：简易 XML，两件独立货号。
- **Delta Drift Lab（drift）**：JSONL，1 行合规 + 缺必填 / 类型不符 / 未声明字段各 1 行，用于演练结构漂移拦截。

打开“运行台”选源跑一次增量，再看 Staging / 冲突 / 结构漂移 / Canonical 四页计数是否与本次 Run 报告一致。

## 源级字段契约（schema_drift_guard）

每个源在“字段契约”页维护一份期望 Schema：字段是否必填 + 期望类型
（`string` / `number` / `integer` / `boolean`），并可切换是否允许未声明字段。

增量与全量运行时，每条解析记录先过契约门禁，再走水位线与业务键归并：

| 漂移类型 | 触发条件 | 处置 |
| --- | --- | --- |
| `missing_required` | 必填字段缺失或为空 | 记结构漂移，不进 Staging 归并 / Canonical |
| `type_mismatch` | 字段值无法解释为期望类型 | 同上 |
| `undeclared_field` | 出现契约未声明字段（且未开启允许） | 同上 |

漂移行（含原始夹具记录与逐字段期望/实际对比）在“结构漂移”页按运行查看。
运行报告中的 parsed = staging 处置行 + 漂移行；`/api/runs/{id}/reconciliation`
与“Staging”页统计条都由数据库实时聚合，报告数字与列表页同源可对账。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 0-1 切口（未实现）

`lookback_backfill` / `dead_letter_replay` / `webhook_push_source`
