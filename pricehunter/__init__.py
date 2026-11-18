"""商品价格采集工具（本地夹具模拟版）。

模块划分：
- timeutil/model: 可注入时钟与领域模型
- minisoup:       标准库实现的极简 HTML 解析
- storage:        SQLite 双时态存储（快照 / 价格版本 / 有效区间 / 促销 / 告警）
- fixture_server: 本地多站点夹具 HTTP 服务器
- transport:      带类型化异常的 HTTP 抓取层
- parsers/sources: 带版本的解析器与来源定义
- pipeline:       采集主流程（分页、429 重试、维护页识别）
- rules:          促销命中与异常告警规则
- revision/replay: 历史修订级联重算、快照回放差异
- dashboard:      单页运维界面
"""

__version__ = "1.0.0"
