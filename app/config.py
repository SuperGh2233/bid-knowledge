"""bid-ai-clean 配置：密钥只从环境变量读取，不落库。"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ⚠️ 原先这里**没有**加载 `.env`，只读 `os.environ` —— 于是 README 说的「从 .env.example 复制」
# 建出来的 `.env` **完全不生效**（实测：`LLM_MODEL` 等读出来全是空）。本仓库已有脚本
# 用 `dotenv_values` 的先例（`scripts/ocr_batch.py`），依赖也在 requirements 里，故在此补上。
# `override=False`：**真实环境变量优先**，`.env` 只作兜底（部署时用环境变量注入密钥更安全）。
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=False)
except Exception:  # noqa: BLE001 —— 没装 dotenv 或 .env 不存在时，退回纯环境变量
    pass

# 数据/存储
DB_PATH = os.environ.get("BID_AI_CLEAN_DB", str(BASE_DIR / "bid_ai_clean.db"))
# SOURCE_ROOTS 为空串/全空时过滤，避免空配置被当成当前目录（P0 防护）
SOURCE_ROOTS = tuple(
    r for r in (p.strip() for p in os.environ.get("SOURCE_ROOTS", "").split(os.pathsep))
    if r
)
ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200")
ES_INDEX_SCHEME = os.environ.get("ES_INDEX_SCHEME", "bid_scheme_sections_v1")
ES_INDEX_CONFIG = os.environ.get("ES_INDEX_CONFIG", "bid_config_index_v1")  # 预留：embedding dims 探测

# LLM / OCR（外发需授权；OCR 本地优先）
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")
EMBEDDING_BASE_URL = os.environ.get("EMBEDDING_BASE_URL", LLM_BASE_URL)
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", LLM_API_KEY)
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "")

# 扫描/解析安全
OCR_ENABLED = os.environ.get("OCR_ENABLED", "false").lower() == "true"  # 默认不 OCR
PARSE_NATIVE_FIRST = os.environ.get("PARSE_NATIVE_FIRST", "true").lower() == "true"
DERIVED_PAGE_IMAGE_SKIP = os.environ.get("DERIVED_PAGE_IMAGE_SKIP", "true").lower() == "true"

# OCR（**外发，须单独授权**；授权记录见 docs/ocr-authorization.md）
# 主通道 qwen（与 LLM 同网关），兜底 MinerU（公网）。默认关闭，未授权不得启用。
OCR_BASE_URL = os.environ.get("OCR_BASE_URL", "")
OCR_API_KEY = os.environ.get("OCR_API_KEY", "")
OCR_MODEL = os.environ.get("OCR_MODEL", "")
OCR_TIMEOUT_SECONDS = int(os.environ.get("OCR_TIMEOUT_SECONDS", "180"))
OCR_RENDER_SCALE = float(os.environ.get("OCR_RENDER_SCALE", "2.0"))
OCR_MAX_IMAGE_BYTES = int(os.environ.get("OCR_MAX_IMAGE_BYTES", "14000000"))
OCR_FALLBACK_MINERU = os.environ.get("OCR_FALLBACK_MINERU", "false").lower() == "true"
# OCR 后端：`local` → 本机 rapidocr（**零外发，不受 OCR_ENABLED 守卫约束**）；
# 其他值 → 外发网关（需 OCR_ENABLED=true + 授权记录）。
OCR_BACKEND = os.environ.get("OCR_BACKEND", "qwen")
MINERU_API_BASE_URL = os.environ.get("MINERU_API_BASE_URL", "https://mineru.net")
MINERU_API_TOKEN = os.environ.get("MINERU_API_TOKEN", "")

# 方案生成（**外发，须单独授权**）：把证据包正文交给生成模型。
# 默认关闭 —— 未开启时任何生成入口直接抛错，不"未授权却默默外发"（同 OCR 的做法）。
# 注意：现有两份授权记录**均不覆盖方案章节正文**（docs/llm-classification-authorization.md
# 只授权候选行短文本）。启用前须另立授权记录。本地机械件（召回/聚类/证据包）不需要它。
PROPOSAL_GEN_ENABLED = os.environ.get("PROPOSAL_GEN_ENABLED", "false").lower() == "true"
PROPOSAL_GEN_TIMEOUT = int(os.environ.get("PROPOSAL_GEN_TIMEOUT", "300"))
# LLM **查询意图识别**（2026-09-16 用户选择「调 LLM 做意图识别」）。
# 授权记录：`docs/llm-intent-authorization.md`（只发**用户那一句查询**，不含任何文档正文）。
# **默认 false** —— 关闭时走本地确定性识别，与改前行为一致，且不会有任何外发。
INTENT_LLM_ENABLED = os.environ.get("INTENT_LLM_ENABLED", "false").lower() == "true"
INTENT_LLM_TIMEOUT = int(os.environ.get("INTENT_LLM_TIMEOUT", "20"))
# 提示词预算（字符）。方案模块全开时证据正文合计可达 5 万字量级，可能超模型上下文；
# 超预算时按比例收缩每条摘录，并在 payload.truncation 里如实记录（见 packs_to_payload）。
PROPOSAL_GEN_MAX_PROMPT_CHARS = int(os.environ.get("PROPOSAL_GEN_MAX_PROMPT_CHARS", "40000"))

# 文件打开（`POST /api/open`）：在结果卡片上"直接打开源文件/所在文件夹"。
# **默认关闭**（fail-closed，同 OCR 与方案生成的做法）——这是本服务**第一条会启动外部程序**
# 的代码路径，关闭时端点直接 403，不"没开却默默做事"。
# ⚠️ 只读约束不变：本功能只"打开/定位"，**不写、不改、不移动共享盘上的任何东西**。
OPEN_EXTERNAL_ENABLED = os.environ.get("OPEN_EXTERNAL_ENABLED", "false").lower() == "true"
# 只允许本机（回环）访问该端点 —— 不依赖"服务只 bind 127.0.0.1"这一件事的正确性。
# 误 bind 到 0.0.0.0 时，它挡住"外人远程启动你机器上的程序"。
OPEN_EXTERNAL_ONLY_LOOPBACK = os.environ.get("OPEN_EXTERNAL_ONLY_LOOPBACK", "true").lower() == "true"

# **合同明细金额：数量×单价 推导**（**默认开启**，2026-09-11 按计划 §2 修订落地）。
# 计划 §2 原先只认「金额列」，2026-09-11 按「验收指标需要变化须先改本文档并说明原因」的流程修订为：
#   `金额` 列明写 → 取其值；列为空但**整表**每条明细都有 数量 与 单价 → 取 数量×单价；两者都无 → 仍返回「未确认」。
# 依据（四条，均非为达标）：① 提升准确率（与文件名成交价一致 80→82，唯一新增不符为 0.00005% 舍入残差）；
# ② 是「读取」非「猜测」（前提是整表完整，串列表已被排除）；③ 参照系统本就用此规则
# （金标准 `87000` vs 我们 OCR `870000`，`58×1500=87,000`）；④ 推算值全程带标记，不与明写值混淆。
# 回退：设 `CONTRACT_DERIVE_AMOUNT=false` 即恢复旧口径（Recall 92.3% → 88.5%）。
CONTRACT_DERIVE_AMOUNT = os.environ.get("CONTRACT_DERIVE_AMOUNT", "true").lower() == "true"


def required(name: str, value: str | None) -> str:
    if not value:
        raise RuntimeError(f"环境变量缺失：{name}")
    return value