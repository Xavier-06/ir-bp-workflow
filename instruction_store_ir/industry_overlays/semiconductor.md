# Industry Overlay: 半导体 / 芯片 / 消费电子

## 产业链定位（第一步必做：先判定标的属于哪个环节）

| 环节 | 判定信号 | 核心指标（只用这段）| 估值范式倾向 |
|------|---------|------------------|------------|
| **上游·设计 Fabless** | 无晶圆厂、IP/算法驱动（寒武纪/海光/韦尔/高通）| design-win 数、流片进度、客户导入、ASP×量、研发费用率 | preprofit_growth / profitable_growth |
| **中游·制造 Foundry/IDM** | 有晶圆厂、产能驱动（中芯/华虹/台积电）| 产能利用率、ASP、capex、良率、折旧/收入比 | cyclical_asset |
| **上游·设备/材料** | 卖铲子（北方华创/中微/沪硅/ASML）| 订单 backlog、国产化率、客户 capex 计划 | profitable_growth |
| **下游·封测** | 封装测试（长电/通富/日月光）| 先进封装渗透率、CoWoS 产能、稼动率 | cyclical_asset |
| **终端·消费电子** | 品牌整机（苹果/小米/传音）| 出货量、ASP、BOM 成本、渠道库存 | profitable_growth |

> ⚠️ 判定环节后，下方「核心分析框架」只展开对应环节的指标，不要全段堆砌。
> ⚠️ 环节判定与 research_plan 的 valuation_paradigm 交叉验证：设计公司不该用 cyclical_asset 框架。

## 核心分析框架

### 产能周期（必查）
- 当前产能利用率 / 排产数据（月度高频）
- 资本开支周期（capex YoY、新增产线投产时间表）
- 库存周期（渠道库存天数、DOI 变化趋势）
- 搜索关键词：`{entity} 排产 {年月}`、`{entity} 产能利用率`、`{entity} 库存天数 DOI`

### 技术迭代（必查）
- 当前主力制程/工艺节点 vs 下一代
- 良率爬坡进度（直接影响毛利率）
- 客户验证/导入进度（design-in → design-win → ramp）
- 搜索关键词：`{entity} 良率 制程`、`{entity} design-win`、`{entity} 客户导入 验证`

### 涨价/降价传导（必查）
- 哪些产品在涨/降？幅度多少？
- 交期变化（缩短 = 供需缓解，延长 = 紧缺）
- 标的的定价权（能否转嫁成本）
- 搜索关键词：`{产品名} 涨价 交期 {年月}`、`{entity} ASP 变化`

### 供应链关键指标
- 上游：关键材料/设备供应商集中度
- 中游：封测产能、先进封装渗透率
- 下游：终端需求（手机/PC/服务器/AI 各占比）
- 搜索关键词：`{entity} 供应商 集中度`、`{entity} 终端应用 占比`

## MCP 优先调用
- `westock-mcp.data_finance` → 拉分部收入（按产品线拆分）
- `westock-mcp.data_consensus` → 盈利预测修正方向
- `westock-mcp.data_fund_flow` → 北向资金对半导体板块的流向

## 特有陷阱
- ❌ 不要把设计公司和晶圆厂混为一谈（Fabless vs Foundry vs IDM）
- ❌ 不要忽略库存减值风险（DRAM/NAND 价格下行周期）
- ❌ 不要只看收入增速，要看 ASP × 出货量 的拆解
