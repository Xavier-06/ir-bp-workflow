# Industry Overlay: 互联网 / TMT / SaaS

## 产业链定位（第一步必做：先判定标的属于哪种商业模式）

| 类型 | 判定信号 | 核心指标（只用这段）| 估值范式倾向 |
|------|---------|------------------|------------|
| **平台型** | 双边网络、流量变现（腾讯/美团/Meta）| DAU/MAU、ARPU、take rate、ad load、LTV/CAC | platform_two_sided / profitable_growth |
| **模型公司** | 大模型/AI 服务（GLM/MiniMax/OpenAI）| token 经济学（成本/收入）、ARR、算力成本、融资跑道、推理效率 | preprofit_growth |
| **电商/零售** | GMV 驱动（拼多多/Shopify/京东）| GMV、take rate、履约成本、UE 单均利润 | profitable_growth / platform_two_sided |
| **SaaS/企业服务** | 订阅收入（Salesforce/有赞/金山办公）| NDR、Rule of 40、ARR 增速、CAC 回收期、毛利率 | profitable_growth / preprofit_growth |
| **内容/游戏** | IP/内容变现（网易/迪士尼/泡泡玛特）| 流水、ARPPU、内容生命周期、IP 集中度 | profitable_growth |

> ⚠️ 判定类型后，下方「核心分析框架」只展开对应类型的指标。
> ⚠️ 类型判定与 research_plan 的 valuation_paradigm 交叉验证：模型公司不该用 platform_two_sided 框架。
> ⚠️ 模型公司 ≠ 平台型：GLM/MiniMax 核心是 token 经济学 + 算力成本 + ARR + 融资跑道，不是 DAU/take rate。

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
