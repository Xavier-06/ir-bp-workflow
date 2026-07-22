# Industry Overlay: 互联网 / TMT / SaaS

## 核心分析框架

### 用户增长与留存（必查）
- DAU/MAU 增速、用户时长变化
- 获客成本（CAC）及回收期
- 用户分层：高价值 vs 低价值用户占比
- 搜索关键词：`{entity} DAU MAU 用户增长`、`{entity} 用户时长`、`{entity} 获客成本 CAC`

### 变现效率（必查）
- ARPU / ARPPU 趋势
- 广告 vs 电商 vs 增值服务收入占比及增速
- Take rate（平台抽成率）变化
- 搜索关键词：`{entity} ARPU 变现`、`{entity} take rate`、`{entity} 收入拆分`

### AI 落地与第二曲线（必查）
- AI 产品/功能的商业化进展（付费用户、ARR）
- AI 对存量业务的效率提升（降本增效量化）
- 竞争格局：大厂 vs 创业公司在该赛道的份额
- 搜索关键词：`{entity} AI 大模型 商业化`、`{entity} AI ARR`、`{entity} 降本增效`

### 监管与合规（必查）
- 当前监管态度（收紧/放松/中性）
- 数据合规、反垄断、算法备案等
- 出海地缘政治风险
- 搜索关键词：`{entity} 监管 反垄断`、`{entity} 数据安全 合规`

## MCP 优先调用
- `westock-mcp.data_finance` → 收入拆分（按业务线）、研发费用率
- `westock-mcp.data_consensus` → 盈利预测（互联网公司利润波动大）
- `westock-mcp.data_rating` → 评级分布（互联网板块分歧大）

## 特有陷阱
- ❌ 不要只看 GMV，要看 take rate 和变现后的实际收入
- ❌ 不要忽略股权激励费用（SBC 对真实利润的影响）
- ❌ 不要把一次性投资收益当经营利润
- ❌ 用户增长见顶后，ARPU 提升能否接力是关键问题
