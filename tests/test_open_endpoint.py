"""`POST /api/open` 的护栏：**逐条钉住拒绝路径**。

为什么这个端点的测试格外重要（2026-09-14）：它是本服务**第一条会启动外部程序**的代码路径
（全仓此前无 `os.startfile` / `subprocess` 先例），安全校验全部自建、无先例可抄。
所以"拒绝"必须写成断言，而不是靠评审记得。

关键机制 —— **石蕊函数**：`monkeypatch` 掉 `routes_open._launch_external`，一旦被调用就抛错。
任何一条拒绝路径都必须**在触达启动点之前**停下；这把这个不变量从注释变成了断言。

不需要 ES / key / 真实共享盘：库与根目录都用 `tmp_path` 造。
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.api as A  # noqa: E402
import app.config as config  # noqa: E402
import app.routes_open as routes_open  # noqa: E402

DOC_OK = "a" * 64                    # 合法：64 位十六进制
DOC_ABSENT = "b" * 64                # 合法格式，但库里没有


@pytest.fixture
def env(tmp_path, monkeypatch):
    """合成库 + 临时根目录 + 三道闸放开 + 石蕊函数。返回 (client, root, spy)。"""
    db = tmp_path / "open.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE documents (document_id TEXT PRIMARY KEY, relative_path TEXT,"
                " source_root_id TEXT, document_role TEXT, parse_status TEXT)")
    # 正常的已登记文件
    con.execute("INSERT INTO documents VALUES (?,?,?,?,?)",
                (DOC_OK, "项目甲/合同/响应文件.docx", "2026年", "our_response", "pending"))
    con.commit()
    con.close()
    monkeypatch.setattr(A, "DEMO_DB", db)

    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setattr(routes_open, "DEFAULT_ROOTS", {"2026年": root})

    monkeypatch.setattr(config, "OPEN_EXTERNAL_ENABLED", True)
    monkeypatch.setattr(config, "OPEN_EXTERNAL_ONLY_LOOPBACK", True)
    # TestClient 的 request.client.host 是 "testclient"，会撞回环闸门 —— 放行（闸本身另有用例）
    monkeypatch.setattr(routes_open, "_client_is_loopback", lambda request: True)

    called: list = []

    def spy(full, target):
        called.append((full, target))
        raise AssertionError("拒绝用例不得触达启动点")

    monkeypatch.setattr(routes_open, "_launch_external", spy)
    return TestClient(A.app), root, called


def _open(client, document_id=DOC_OK, target="folder"):
    return client.post("/api/open", json={"document_id": document_id, "target": target})


def _put(db_document_id, relative_path, source_root_id, tmp_path, monkeypatch):
    """改一条已登记文件的路径/根（用于注入恶意相对路径或未知根）。"""
    db = A.DEMO_DB
    con = sqlite3.connect(db)
    con.execute("DELETE FROM documents")
    con.execute("INSERT INTO documents VALUES (?,?,?,?,?)",
                (db_document_id, relative_path, source_root_id, "our_response", "pending"))
    con.commit()
    con.close()


# —— 拒绝用例：每一条都断言「状态码 + 中文理由 + 没触达启动点」——

def test_document_id_format_rejected_before_any_lookup(env):
    client, _, called = env
    for bad in ("", "not-a-hash", "A" * 64, "a" * 63, "a" * 65, "../etc/passwd"):
        assert _open(client, document_id=bad).status_code == 400
    assert called == []


def test_unregistered_document_id_404(env):
    client, _, called = env
    r = _open(client, document_id=DOC_ABSENT)
    assert r.status_code == 404 and "未登记" in r.json()["detail"]
    assert called == []


def test_unknown_target_400(env):
    client, _, called = env
    r = _open(client, target="exe")
    assert r.status_code == 400
    assert called == []


def test_unknown_source_root_raises_and_never_degrades_to_cwd(env, tmp_path, monkeypatch):
    """**关键回归**：未知 `source_root_id` 必须抛错。

    全仓现有拼法是 `roots.get(id, Path(""))` → 静默退化成「相对于进程 CWD 的路径」，
    那等于把"配置缺失"变成"打开工作目录下的同名文件"。此处钉死不得退化。
    """
    client, _, called = env
    _put(DOC_OK, "a.docx", "2099年", tmp_path, monkeypatch)
    r = _open(client)
    assert r.status_code == 500, r.text
    assert "未配置" in r.json()["detail"]
    assert called == []


@pytest.mark.parametrize("bad_rel", [
    "项目/../机密/a.docx",          # 上级目录
    "/etc/passwd",                  # 绝对路径（正斜杠）
    "\\\\srv\\share\\a.docx",       # UNC 注入
    "C:/Windows/a.docx",            # 盘符绝对路径
    "项目/a.docx\\..\\b.docx",      # 备用分隔符
    "项目/con.docx",                # Windows 保留设备名
    "项目/a.docx.",                 # 段尾点（Windows 会静默 strip → 别名逃逸）
])
def test_malicious_relative_path_rejected(env, tmp_path, monkeypatch, bad_rel):
    client, _, called = env
    _put(DOC_OK, bad_rel, "2026年", tmp_path, monkeypatch)
    assert _open(client).status_code == 400, bad_rel
    assert called == []


def test_legit_filename_with_double_dot_is_not_rejected(env, tmp_path, monkeypatch):
    """**防「子串判 ..」的回归锁**：库内实测有 2 个**合法文件名**含 `..`。

    子串判会把它们全部误拒 —— 本条与上一条一起，把"按 component 判"这个要求钉死。
    （校验通过后会走到存在性检查：tmp 下没有这个文件 → 404，而不是 400。）
    """
    client, _, called = env
    for name in ("项目/欧易报名文件..docx", "项目/欧易项目负责人..docx"):
        _put(DOC_OK, name, "2026年", tmp_path, monkeypatch)
        r = _open(client)
        assert r.status_code == 404, f"{name} 被误拒为 {r.status_code}：{r.text}"
    assert called == []


@pytest.mark.parametrize("suffix", [".exe", ".lnk", ".bat", ".ps1", ".scr", ".msi"])
def test_blocked_suffix_never_opened(env, tmp_path, monkeypatch, suffix):
    """库内实测 6 个 `.exe`（投标客户端安装包）；`os.startfile` 对 `.exe` 是**执行**。"""
    client, _, called = env
    _put(DOC_OK, f"项目/安装程序{suffix}", "2026年", tmp_path, monkeypatch)
    r = _open(client)
    assert r.status_code == 400 and "不能在页面上打开" in r.json()["detail"]
    assert called == []


def test_non_document_suffix_for_file_target_degrades_with_guidance(env, tmp_path, monkeypatch):
    """`target=file` 的白名单外后缀 → 拒绝，且**必须给出降级指引**（不能是死胡同）。"""
    client, root, called = env
    (root / "项目").mkdir(parents=True, exist_ok=True)
    (root / "项目" / "包.zip").write_bytes(b"PK")
    _put(DOC_OK, "项目/包.zip", "2026年", tmp_path, monkeypatch)
    r = _open(client, target="file")
    assert r.status_code == 400
    assert "复制路径" in r.json()["detail"]
    assert called == []


def test_root_unreachable_503_distinct_from_missing_404(env, tmp_path, monkeypatch):
    """**可达性与存在性必须分开报**。

    实测本机 `\\\\192.168.10.188\\...` 的 `exists()` 就是 False —— 那是盘不可达，
    说成「文件不存在」是事实性误导，处置方式也完全不同（一个去连网络，一个是数据过时）。
    """
    client, root, called = env
    # (a) 根不存在 → 503，且**不得**说成"文件不存在"
    monkeypatch.setattr(routes_open, "DEFAULT_ROOTS", {"2026年": tmp_path / "不存在的盘"})
    r = _open(client)
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert ("不可达" in detail or "访问权限" in detail) and "不在共享盘上" not in detail
    # (b) 根在、文件不在 → 404
    monkeypatch.setattr(routes_open, "DEFAULT_ROOTS", {"2026年": root})
    r = _open(client)
    assert r.status_code == 404 and "不在共享盘上" in r.json()["detail"]
    assert called == []


def test_switch_off_403(env, monkeypatch):
    client, _, called = env
    monkeypatch.setattr(config, "OPEN_EXTERNAL_ENABLED", False)
    r = _open(client)
    assert r.status_code == 403 and "未开启" in r.json()["detail"]
    assert called == []


def test_non_loopback_403(env, monkeypatch):
    client, _, called = env
    monkeypatch.setattr(routes_open, "_client_is_loopback", lambda request: False)
    r = _open(client)
    assert r.status_code == 403
    assert called == []


# —— 唯一允许触达启动点的用例 ——

def test_success_calls_launch_exactly_once_with_registered_path(env, tmp_path, monkeypatch):
    client, root, called = env
    target_file = root / "项目甲" / "合同" / "响应文件.docx"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_bytes(b"x")

    def spy(full, target):
        called.append((full, target))

    monkeypatch.setattr(routes_open, "_launch_external", spy)

    for target in ("folder", "file"):
        called.clear()
        r = _open(client, target=target)
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True and r.json()["file_name"] == "响应文件.docx"
        assert called == [(str(target_file), target)], called
    # 响应体**不回显绝对路径**（客户端本来就有 source_path，没必要再给一次）
    assert "share" not in r.text


# —— 静态守卫（无网、纯正则）：把"不可执行、有黑名单、两表不相交"钉在源码上 ——

def test_source_has_no_shell_execution_paths():
    src = (REPO / "app" / "routes_open.py").read_text(encoding="utf-8")
    for pattern in (r"shell\s*=\s*True", r"os\.system\s*\(", r"subprocess\.run\s*\(",
                    r"subprocess\.call\s*\(", r"subprocess\.check_output\s*\("):
        assert not re.search(pattern, src), f"routes_open.py 出现被禁用的执行方式：{pattern}"
    # Popen 只允许列表参数形式
    for m in re.finditer(r"subprocess\.Popen\(\s*([^,)]+)", src):
        assert m.group(1).strip().startswith("["), f"Popen 必须用列表参数：{m.group(0)}"


def test_suffix_sets_are_sane():
    assert ".exe" in routes_open.BLOCKED_SUFFIXES       # 纵深防御层，不得被后人误删
    assert not (routes_open.BLOCKED_SUFFIXES & routes_open.FILE_OPENABLE_SUFFIXES)
    # 宏格式刻意排除（打开即可能执行宏）
    assert not (routes_open.FILE_OPENABLE_SUFFIXES & {".docm", ".xlsm", ".pptm", ".xlsb"})