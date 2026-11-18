# PriceHunter — 商品价格采集工具（本地夹具模拟版）

纯 Python 标准库实现（无第三方依赖），用本地 HTML / JSON 夹具模拟三个站点，
覆盖分页、动态字段、429 限流、页面结构变化、维护页识别与历史价格回溯修订。

## 快速开始

```bash
# 终端 1：启动夹具站点（三个模拟来源）
python3 -m pricehunter.cli serve-fixtures --port 8088

# 终端 2：运行六天场景的端到端演示（自动完成采集→回放→升级→回溯）
python3 -m pricehunter.cli run-demo

# 查看汇总状态（健康度 / 告警 / 促销 / 修订 / 当前价）
python3 -m pricehunter.cli status

# 启动运维仪表盘
python3 -m pricehunter.cli dashboard --port 8090
# 浏览器打开 http://127.0.0.1:8090
```

测试：

```bash
python3 -m unittest discover -s tests -v
```

## 场景时间线（模拟日期 2026-11-13 ~ 2026-11-18）

| 日期 | acme（HTML 分页） | bargain（JSON 游标） | ceg（维护频发） |
|---|---|---|---|
| D1 11-13 | 正常 | 正常 | 正常 |
| D2 11-14 | SKU-1001 错误价 499 | 前两次请求 **429**，退避重试后成功 | 正常 |
| D3 11-15 | 正常 | 新增动态字段 `list_price`/`promo`（解析器向后兼容） | **200 软维护页** |
| D4 11-16 | 页面升级 v2，旧解析器报 `structure_changed` | 正常 | **503 硬维护** |
| D5 11-17 | v1 持续失败 | SKU-2003 价格尖峰（异常告警） | 恢复 |
| D6 11-18 | 升级 v2 解析器重跑成功 | 正常 | 正常；随后归档修正 11-14 价格 |

## 核心设计

### 1. 原始快照不可变
每次抓取（包括成功、429、维护、错误）都把原始响应体落盘到 `data/snapshots/`，
`snapshots` 表只存路径与哈希，快照永不更新。解析失败另存可读样本到
`snapshots/failures/`，仪表盘可直接查看原始证据。

### 2. 维护页 ≠ 空结果
- 硬维护：HTTP 503 + `Retry-After` → `Maintenance` 异常，快照 outcome=`maintenance`；
- 软维护：HTTP 200 但页面带显式标记（`data-page="maintenance"` /
  `<meta name="page-state">`），传输层在解析前拦截；
- 合法空页：列表容器存在但零商品节点，仅作为分页终止信号，outcome 仍是 `ok`。
维护会立即中止该来源本次采集并产生来源级告警，绝不写入零价格或当作"没商品"。

### 3. 解析器版本化
解析器带显式版本号（`acme-parser-v1/v2`、`bargain-parser-v1`、`ceg-parser-v1`），
结构不匹配时抛 `StructureChanged` 并记录 `parse_attempts`，而不是吐出半残数据。
升级解析器前可先做**只读快照回放**，输出字段级差异（`replays` / `replay_diffs`），
回放不触碰任何生产价格数据。

### 4. 双时态价格版本链
`price_versions` 同时记录：
- 业务时间 `observed_date`（价格有效区间维度）；
- 系统时间 `recorded_at`（何时入库）；
- `revision_no` + `superseded_by`（修订链）。

同一观测日重复入库幂等；值变化时旧版本置 `superseded`、写入新版本并留
`revisions` 审计行，价格的完整历史链路可逐版本回放。

### 5. 回溯修订级联重算
归档回填（`backfill`）走标准修订路径修正历史价格：旧版本作废 → 新版本生效 →
该 (SKU, 观测日) 的促销命中与该 SKU 全序列异常告警同步重算，旧派生行置
`superseded` 而非删除，保证"当时为什么报警/命中促销"永远可追溯。

### 6. 单来源隔离
每个来源一次运行 = 独立 `run_id` + 独立 `Transport`（独立重试计数）。
仪表盘"单来源重跑"只新建该来源的 run、只读写该来源的行，其他来源 run 数与
价格版本零变化（有测试断言保证）。

### 7. 429 处理
按 `Retry-After` / 指数退避重试；即使最终重试成功，途中每个 429 响应也单独
落盘为 `rate_limited` 快照，限流历史可追溯；重试耗尽则本次运行标记 `failed`，
不会被静默当成空页。

## 模块结构

```
pricehunter/
├── fixtures/        # 本地 HTTP 夹具：场景时间线、HTML/JSON 生成、429/503/软维护
│   ├── catalog.py   #   确定性商品数据与六天故障脚本
│   └── server.py    #   http.server 路由（X-Sim-Date 选择场景日期）
├── timeutil.py      # 可注入时钟（演示/测试时间确定）
├── model.py         # 枚举与数据类
├── minisoup.py      # 标准库 html.parser 的极简 DOM
├── transport.py     # 抓取层：类型化异常、429 退避、软维护识别
├── parsers/         # 版本化解析器注册表
├── sources.py       # 来源声明
├── storage.py       # SQLite 双时态存储 + 线程安全锁包装连接
├── rules.py         # 促销命中（折扣阈值）+ 异常告警（z-score）
├── revision.py      # 幂等入库与历史修订
├── replay.py        # 只读快照回放与字段级差异
├── backfill.py      # 归档修正快照回填
├── pipeline.py      # 采集主流程（分页/重试/快照/解析/重算）
├── demo.py          # 六天端到端编排
├── dashboard_server.py  # 仪表盘 JSON API + 动作端点
├── webapp.py        # 单页前端（原生 HTML/CSS/JS）
└── cli.py           # 命令行入口
tests/               # 15 个单元/端到端测试（含真实 HTTP 夹具）
```

## 仪表盘四个视图

1. **来源健康度**：快照成功率、429 数、维护拦截数、解析失败数、最近抓取；
   卡片上可直接单来源重跑、升级解析器、归档回溯。
2. **解析失败样本**：错误类型、解析器版本、原始 URL，一键查看原始快照。
3. **回放差异**：选择解析器版本对历史快照只读重放，展示结构变化与字段差异。
4. **当前价格链路**：促销命中、异常/维护告警、修订记录，以及每个 SKU 的
   双时态版本链（当前版本 / 已修订版本、观测日、修订号、解析器、快照溯源）。

## 常用命令

```bash
python3 -m pricehunter.cli collect --source acme           # 单来源采集
python3 -m pricehunter.cli replay  --source acme --parser-version acme-parser-v2
python3 -m pricehunter.cli backfill --source acme --archive-date 2026-11-14
python3 -m pricehunter.cli status
```

数据默认落在 `./data/`（可用 `--data-dir` 或 `PRICEHUNTER_DATA` 覆盖）。
