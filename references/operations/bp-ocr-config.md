# BP OCR 配置

## VL OCR 识别

- **Model**: `qwen3-vl-30b-a3b-instruct`（OpenAI-compatible API）
- **API Base**: `https://api.tokenpony.cn/v1`
- **API Key**: 通过 `BP_OCR_API_KEY` 环境变量或 `.credentials/investment-research.env` 文件配置

### 配置加载优先级（代码 `ocr_pdf.py`）

1. 环境变量 `BP_OCR_API_KEY` / `BP_OCR_MODEL` / `BP_OCR_BASE_URL`
2. 项目 `.credentials/investment-research.env` 文件中同名 key
3. model 默认值 `qwen3-vl-30b-a3b-instruct`，base_url 无默认值（必须配置）

### .credentials/investment-research.env 格式

```env
BP_OCR_API_KEY=sk-xxx
BP_OCR_BASE_URL=https://api.tokenpony.cn/v1
BP_OCR_MODEL=qwen3-vl-30b-a3b-instruct
```

## 支持格式

PDF / PPTX / DOCX / 图片

## 输出

- `bp_ocr_text.txt` — OCR 提取全文
- `bp_step0_profile.json` — 结构化抽取结果

## 自动提取字段

公司名 / 行业 / 融资阶段 / 商业模式 / 团队 / 财务 / 竞争优势

## Fallback

qwen-vl 不可用时自动降级为 Tesseract OCR（`pdf2image` + `pytesseract`）

## 融资阶段判断规则（硬性约束）

| 阶段 | 判断标准 |
|------|---------|
| 种子轮 | 仅想法/原型，无产品无用户无营收，团队可能不完整 |
| 天使轮 | 产品刚上线有少量用户但无稳定收入 |
| Pre-A | 产品有初步验证，小规模用户/收入，商业模式未验证 |
| A轮 | 产品市场验证，稳定用户和增长，商业模式基本跑通 |
| B轮 | 商业模式成熟，规模化扩张，明显营收增长 |
| C轮+ | 行业头部，盈利或接近盈利 |
| Pre-IPO | 满足上市条件，正在IPO申报 |

**核心原则：搜不到公开工商/财报信息 = 绝不可能是Pre-IPO/C轮+；零营收 = 不可能是B轮+**
