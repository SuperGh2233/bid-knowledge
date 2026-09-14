"""扫描件 OCR（**外发操作，须单独授权**）。

授权依据：`docs/ocr-authorization.md`（2026-09-10 用户选择方案 A）。
  - 主体范围：`document_role='contract_evidence'` 的扫描 PDF / 图片；
  - 主通道：`.env` 的 OCR/LLM 网关（`https://newapi.oebiotech.com/v1`，qwen）；
  - 兜底：`https://mineru.net`（**公网第三方**，仅在 qwen 失败且显式开启时使用）。

设计约束：
  - **默认关闭**。`config.OCR_ENABLED` 为 false 时，任何 OCR 入口直接抛错，
    避免"未授权却默默外发"。
  - **本地后端（`OCR_BACKEND=local`，2026-09-11 新增）不受该守卫约束** ——
    它用 `rapidocr`（PP-OCRv6，onnxruntime）在**本机**跑，**根本不外发**，
    故不存在「把正文送出去要不要授权」的问题。实测 ~9–16 s/页（网关约 3–6 s/页），
    慢但换来的是**不需要任何外发授权** —— 这是外发被拦时的 safer method。
  - 页面级失败不丢整篇：如实记录失败页，不伪装成功。
  - 不做全本图片 OCR 兜底扩张；调用方决定处理哪些文件。
"""
from __future__ import annotations

import base64
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path

import app.config as config

_OCR_PROMPT = """请逐字转写这张投标/合同文档页面中真实可见的全部文字。
只输出转写结果，不要总结、解释或补充；保持原文阅读顺序和段落。
表格按行输出，单元格之间使用“ | ”分隔；印章、签字和手写内容能辨认时也要转写。
无法辨认的字符写作 [UNK]，不要猜测，不要使用 Markdown 代码块。"""


class OCRNotAuthorized(RuntimeError):
    """未开启 OCR 时的显式拒绝。"""


class OCRServiceError(RuntimeError):
    """远程 OCR 服务调用失败。"""


@dataclass
class OCRResult:
    text: str = ""
    page_count: int = 0
    pages: list[dict] = field(default_factory=list)   # [{page, chars, method|error}]
    methods: set[str] = field(default_factory=set)

    @property
    def ok_pages(self) -> int:
        return sum(1 for p in self.pages if p.get("chars"))


def _guard() -> None:
    if not config.OCR_ENABLED:
        raise OCRNotAuthorized(
            "OCR 未授权/未开启（config.OCR_ENABLED=false）。"
            "启用前须有书面授权，见 docs/ocr-authorization.md。")


def _qwen_config() -> tuple[str, str, str]:
    base = config.OCR_BASE_URL or config.LLM_BASE_URL
    key = config.OCR_API_KEY or config.LLM_API_KEY
    model = config.OCR_MODEL or config.LLM_MODEL
    if not (base and key and model):
        raise OCRServiceError("缺少 OCR/LLM 网关配置")
    return base, key, model


def _client():
    from openai import OpenAI
    base, key, _ = _qwen_config()
    return OpenAI(base_url=base, api_key=key, timeout=config.OCR_TIMEOUT_SECONDS, max_retries=2)


def recognize_image(image: bytes, mime_type: str = "image/jpeg") -> tuple[str, str]:
    """单张图 → (文字, 方法)。qwen 优先，失败且开启兜底时走 MinerU。"""
    _guard()
    if len(image) > config.OCR_MAX_IMAGE_BYTES:
        raise OCRServiceError(f"图片过大：{len(image)} bytes")
    _, _, model = _qwen_config()
    data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
    try:
        resp = _client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": _OCR_PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]}],
            temperature=0,
            max_tokens=8192,
            extra_body={"enable_thinking": False, "vl_high_resolution_images": True},
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise OCRServiceError("模型未返回文字")
        return text, "qwen"
    except Exception as exc:  # noqa: BLE001
        if not config.OCR_FALLBACK_MINERU:
            raise OCRServiceError(str(exc)[:200]) from exc
        raise OCRServiceError(f"qwen 失败且 MinerU 兜底未实现：{str(exc)[:160]}") from exc


def _render_pdf_page(page) -> bytes:
    import fitz
    for scale in dict.fromkeys((config.OCR_RENDER_SCALE, 1.5, 1.0)):
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        img = pix.tobytes("jpeg", jpg_quality=90)
        if len(img) <= config.OCR_MAX_IMAGE_BYTES:
            return img
    raise OCRServiceError("页面渲染后仍超图片上限")


# ============================================================================
# 本地 OCR 后端（**零外发**，故**不需要授权守卫**）
# ============================================================================
# 为什么单独一条：`_guard()` 防的是「未授权却默默外发」。本地引擎**根本不外发**，
# 把正文送出去的授权问题在它身上不存在，所以它不该被那道守卫挡住。
# 速度慢（实测 ~9–16 s/页，网关约 3–6 s/页），但换来的是**不需要任何外发授权**。
_LOCAL_ENGINE = None


def _local_engine():
    global _LOCAL_ENGINE
    if _LOCAL_ENGINE is None:
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:  # noqa: BLE001
            raise OCRServiceError(
                "本地 OCR 不可用：未安装 rapidocr。安装或改用 OCR_BACKEND=qwen（需外发授权）") from exc
        _LOCAL_ENGINE = RapidOCR()
    return _LOCAL_ENGINE


def use_local_backend() -> bool:
    return (config.OCR_BACKEND or "").strip().lower() == "local"


def recognize_image_local(image: bytes) -> tuple[str, str]:
    """单张图 → (文字, 方法)。**全程本地，不联网。**"""
    res = _local_engine()(image)
    txts = getattr(res, "txts", None)
    if not txts:
        return "", "rapidocr"
    return "\n".join(txts), "rapidocr"


def ocr_pdf(path: Path, max_pages: int | None = None) -> OCRResult:
    """扫描 PDF 逐页 OCR。页面级失败不丢整篇，如实记录。

    后端由 `config.OCR_BACKEND` 决定：`local` → 本地 rapidocr（**零外发、无需守卫**）；
    其余 → 外发网关（**需要授权，`_guard()` 把关**）。
    """
    local = use_local_backend()
    if not local:
        _guard()
    import fitz
    out = OCRResult()
    parts: list[str] = []
    with fitz.open(str(path)) as doc:
        out.page_count = doc.page_count
        limit = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
        for i in range(limit):
            try:
                # ⚠️ `page = doc[i]` **必须在 try 内**：xref 损坏的 PDF 会出现
                # `page_count` 报 N 页、实际取不到第 i 页 → `IndexError: page i not in document`。
                # 放在 try 外会让**整个文件**失败，而它只是单页的问题（实测 1 例）。
                page = doc[i]
                if local:
                    pix = page.get_pixmap(matrix=fitz.Matrix(config.OCR_RENDER_SCALE,
                                                             config.OCR_RENDER_SCALE), alpha=False)
                    text, method = recognize_image_local(pix.tobytes("png"))
                else:
                    text, method = recognize_image(_render_pdf_page(page), "image/jpeg")
                parts.append(text)
                out.methods.add(method)
                out.pages.append({"page": i + 1, "chars": len(text), "method": method})
            except Exception as exc:  # noqa: BLE001
                out.pages.append({"page": i + 1, "chars": 0, "error": str(exc)[:120]})
        if limit < doc.page_count:
            out.pages.append({"page": f">{limit}", "chars": 0,
                              "error": f"未处理：共 {doc.page_count} 页，本轮上限 {limit}"})
    out.text = "\n".join(parts)
    return out


def ocr_image_file(path: Path) -> OCRResult:
    """单张扫描图（jpg/png）OCR。后端同上：本地零外发 / 网关需授权。"""
    local = use_local_backend()
    if not local:
        _guard()
    data = Path(path).read_bytes()
    mime = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
    out = OCRResult(page_count=1)
    try:
        text, method = (recognize_image_local(data) if local
                        else recognize_image(data, mime))
        out.text = text
        out.methods.add(method)
        out.pages.append({"page": 1, "chars": len(text), "method": method})
    except Exception as exc:  # noqa: BLE001
        out.pages.append({"page": 1, "chars": 0, "error": str(exc)[:120]})
    return out
