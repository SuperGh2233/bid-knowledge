"""文件打开：把结果卡片上的「复制文件位置（给IT定位用）」升级为**直接打开**。

## 为什么单独一个文件
⚠️ **这是本服务第一条会启动外部程序的代码路径** —— 全仓此前没有任何
`os.startfile` / `subprocess` 先例（`app/` 内的写动作只有本地 sqlite 与自建 tempfile）。
让它独占一个文件，便于整体审阅；**kill switch = 删掉 `app/api.py` 底部那一行 include**。

## 安全边界（本项目红线：共享盘只读）
- **只接受 `document_id`，绝不接受任何路径字段**。路径一律服务端从库里反查重建 ——
  这样"前端提交任意路径"这一整类风险**从根上不存在**，而不是"接受路径再校验"。
- 只"打开/定位"：**不写、不改、不移动共享盘上的任何东西**。
- 两道闸默认 fail-closed：配置开关 + 回环限制（详见 `app/config.py`）。

## 与既有约定的一致性
`readonly_db` 从 `app.api` 引入（`tests/` 会 monkeypatch `app.api.DEMO_DB`，自己开库会连到
另一个空库 —— `app/config.py:21` 的 `DB_PATH` 与 `app/api.py` 的 `DEMO_DB` 是两个库）。
⚠️ 唯一偏离：开关用 `from app import config` 后 `config.X` 读取，**不能**
`from app.config import OPEN_EXTERNAL_ENABLED` —— 后者把布尔值冻结在导入期，测试无法 monkeypatch。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import config
from app.api import readonly_db
from app.search import DEFAULT_ROOTS

router = APIRouter()

# `document_id` 是 sha256 十六进制主键（64 位）。注意 `APPROVED_DOCUMENT_IDS` 存的是 **12 位前缀**，
# 两种口径别混 —— 那个只用于白名单起头匹配，这里要的是完整主键。
_DOC_ID_RE = re.compile(r"^[0-9a-f]{64}$")

# ① **永不打开**的后缀（黑名单**先判**：保证将来往白名单里加东西时也不会意外放行这些）。
#    `.exe` 是硬要求 —— 库内实测 6 个「投标客户端安装包」，而 `os.startfile` 对 `.exe` 是**执行**。
BLOCKED_SUFFIXES = frozenset({
    ".exe", ".com", ".scr", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp", ".mst", ".lnk", ".url",
    ".reg", ".hta", ".cpl", ".jar", ".pif", ".dll", ".sys", ".ocx", ".chm",
    ".inf", ".ins", ".isp", ".msc", ".sct", ".shb", ".shs", ".ws", ".wsc",
})

# ② `target=file`（**交给系统用默认程序打开**）允许的后缀：只收文档/图片/纯文本。
#    ⚠️ **刻意排除宏格式** `.docm/.xlsm/.pptm/.xlsb` —— 打开即可能执行宏。
#    ⚠️ 也排除压缩包（打开无意义，且不该让外壳去解压）。
FILE_OPENABLE_SUFFIXES = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".md", ".csv", ".wps", ".et", ".dps",
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff",
})

# Windows 保留设备名（不分扩展名）
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
_BAD_CHARS = set('\\:*?"<>|')


class OpenRequest(BaseModel):
    """**只有 document_id 与 target，没有 path** —— 这是本端点的核心安全设计。"""
    document_id: str
    target: str = "folder"          # folder（在资源管理器中定位）| file（用默认程序打开）


def _client_is_loopback(request: Request) -> bool:
    """本端点只允许本机访问。

    抽成**命名函数**是为了可测：`TestClient` 的 `request.client.host` 是 `"testclient"`，
    否则每个用例都得绕过这道闸（测试里 monkeypatch 它，见 tests/test_open_endpoint.py）。
    """
    host = ""
    if request is not None and request.client is not None:
        host = request.client.host or ""
    return host in ("127.0.0.1", "::1", "localhost")


def _resolve_registered_path(document_id: str) -> tuple[str, str, str]:
    """`document_id` → (绝对路径, 文件名, 根目录)。任一步不成立即拒绝，理由用中文回传。"""
    if not _DOC_ID_RE.match(document_id or ""):
        raise HTTPException(400, "document_id 格式不合法（应为 64 位十六进制），拒绝打开")

    with readonly_db() as con:
        row = con.execute(
            "SELECT relative_path, source_root_id FROM documents WHERE document_id=?",
            (document_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "这份文件未登记在册（document_id 可能已变化），无法定位")

    relative_path = str(row["relative_path"] or "")
    source_root_id = str(row["source_root_id"] or "")

    # ⚠️ 未知 root **必须抛错**。全仓现有拼法都是 `roots.get(id, Path(""))` ——
    #    那会把未知 root **静默退化成「相对于进程 CWD 的路径」**，是本项目已记在案的隐患。
    #    正确先例：`app/parser.py::_doc_path()`（未配置就 raise）。
    root = DEFAULT_ROOTS.get(source_root_id)
    if root is None:
        raise HTTPException(
            500, f"source_root_id 未配置：{source_root_id} —— 这是服务端配置缺陷，不是你的操作问题")

    # 相对路径体检：**按 component 判，禁用「子串含 ..」** ——
    # 库内实测有 2 个**合法文件名**含 `..`（`欧易报名文件..docx`、`欧易项目负责人..docx`），
    # 子串判会把它们全部误拒。
    if relative_path.startswith(("/", "\\")) or relative_path.startswith("//") \
            or re.match(r"^[A-Za-z]:", relative_path):
        raise HTTPException(400, "登记的路径不是相对路径，拒绝打开")
    parts = [p for p in relative_path.split("/") if p]
    if not parts:
        raise HTTPException(400, "登记的路径为空，拒绝打开")
    for part in parts:
        if part in (".", ".."):
            raise HTTPException(400, "登记的路径含上级目录，拒绝打开")
        if any(ch in _BAD_CHARS for ch in part) or any(ord(ch) < 32 for ch in part):
            raise HTTPException(400, "登记的路径含非法字符，拒绝打开")
        if part != part.rstrip(". "):
            # Windows 会静默丢掉路径段尾部的点/空格 → 可能指向另一个文件（别名逃逸）
            raise HTTPException(400, "路径段以点或空格结尾，拒绝打开")
        if part.split(".")[0].lower() in _RESERVED_NAMES:
            raise HTTPException(400, "路径段是 Windows 保留设备名，拒绝打开")

    full = Path(str(root)).joinpath(*parts)
    # 包含性断言：**纯词法**（不碰文件系统）。⚠️ 不用 `Path.resolve()` —— 它在存在的路径上会走
    # 真实文件系统查询，共享盘不可达时会挂住。Windows/UNC 大小写不敏感，故 normcase 后比。
    norm_root = os.path.normcase(os.path.normpath(str(root)))
    norm_full = os.path.normcase(os.path.normpath(str(full)))
    if not (norm_full == norm_root or norm_full.startswith(norm_root + os.sep)):
        raise HTTPException(500, "路径越界（内部一致性错误），已拒绝")
    return str(full), parts[-1], str(root)


def _check_reachable_and_present(root: str, full: str) -> None:
    """可达性 → 存在性，**两级分开报**。

    实测：本机 `Path(r'\\\\192.168.10.188\\...\\2025年').exists()` 返回 False 且快速返回 ——
    那是**共享盘不可达**，把它说成「文件不存在」是事实性误导，且处置方式完全不同
    （一个去连网络，一个是数据过时）。
    """
    try:
        root_ok = Path(root).is_dir()
    except OSError:
        root_ok = False
    if not root_ok:
        raise HTTPException(
            503, "共享盘当前不可达或本机没有访问权限 —— 请确认已连公司网络／已映射该共享盘；"
                 "也可以直接用「复制路径」交给 IT（其余功能不受影响）")
    try:
        present = Path(full).is_file()
    except OSError:
        present = False
    if not present:
        raise HTTPException(
            404, "文件不在共享盘上的原位置了（可能已改名或移走）—— 本次结果可能已过时，建议重新检索")


def _launch_external(full: str, target: str) -> None:
    """**唯一副作用点**。

    测试用"石蕊函数"替换它：任何一条拒绝路径都**不得触达这里**（把「校验在启动之前」
    从注释变成断言）。见 `tests/test_open_endpoint.py`。
    """
    if target == "file":
        # 交系统外壳按关联打开（.docx → Word…）。安全性由两层后缀闸门保证：
        # 黑名单先判 + 只收文档/图片/纯文本（且**不含宏格式**）。
        os.startfile(full)          # noqa: S606 —— 路径来自库内登记值，非客户端输入
        return
    # `folder`：只开资源管理器并选中，**不执行文件本体**。
    # ⚠️ explorer.exe 常规返回退出码 1，**不要检查退出码**（会把正常当失败）。
    # ⚠️ 列表参数 + shell=False：路径作为独立 argv 元素，不经任何 shell 解析。
    subprocess.Popen(["explorer", "/select,", full], shell=False)  # noqa: S603


@router.post("/api/open")
def open_registered_file(payload: OpenRequest, request: Request):
    """按 `document_id` 打开源文件 / 定位所在文件夹。

    **只接受 `document_id`** —— 客户端没有构造路径的能力（这正是业务方那句
    「必须由 document_id 查已登记路径，不能让前端提交任意路径」的落地形式）。
    """
    if not config.OPEN_EXTERNAL_ENABLED:
        raise HTTPException(
            403, "文件打开功能未开启（OPEN_EXTERNAL_ENABLED=false）—— "
                 "可以先用「复制路径」交给 IT；其余功能不受影响")
    if config.OPEN_EXTERNAL_ONLY_LOOPBACK and not _client_is_loopback(request):
        raise HTTPException(403, "本功能只允许在本机使用")
    if payload.target not in ("folder", "file"):
        raise HTTPException(400, "target 只能是 folder 或 file")

    full, file_name, root = _resolve_registered_path(payload.document_id)

    suffix = Path(full).suffix.lower()
    if suffix in BLOCKED_SUFFIXES:
        raise HTTPException(400, f"这类文件（{suffix}）不能在页面上打开 —— 请用「复制路径」")
    if payload.target == "file" and suffix not in FILE_OPENABLE_SUFFIXES:
        raise HTTPException(
            400, f"这类文件（{suffix}）不支持用默认程序打开 —— 可以「打开所在文件夹」定位，"
                 f"或用「复制路径」交给 IT")

    _check_reachable_and_present(root, full)
    try:
        _launch_external(full, payload.target)
    except Exception as exc:  # noqa: BLE001 —— 如实报失败，不假装成功
        raise HTTPException(500, f"系统未能打开：{type(exc).__name__}") from exc

    return {
        "ok": True,
        "target": payload.target,
        "file_name": file_name,
        # ⚠️ 只说"我们做了什么"（交给了外壳），**不说"已经打开了"** ——
        # `explorer`/`startfile` 的成败我们无法核实，声称成功就是"报数字前不验口径"的老毛病。
        "message": "已交给资源管理器定位（没看到窗口的话，可能被其他窗口挡住）"
                   if payload.target == "folder"
                   else "已交给系统用默认程序打开（若没反应，可能被安全策略拦下）",
    }