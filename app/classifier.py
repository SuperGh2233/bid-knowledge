"""bid-ai-clean 确定性角色分类器（共享函数，V2）。

明确了分类接口：
    classify(project_folder, relative_path_within_project, {manual_override, project_type})

供应商判定规则（已拍板）：
- project_folder 仅用于范围/审计，不参与供应商判定。
- 供应商目录 =「项目内相对路径」的父目录段，全段规范化等值匹配（“中科没用”≠“中科”）。
- 文件名供应商由文件名规则单独判定。
- 优先级：
    manual_override → 明确文件身份 → 独立供应商目录(父段) → 文件名明确供应商
    → 通用响应关键词 → 过程/报名 → unknown/ambiguous
- 冲突宁可 ambiguous 不强归类。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

OUR_VENDORS = ("欧易", "欧易生物", "oebiotech", "上海欧易", "鹿明", "上海鹿明", "鹿明生物")
COMP_VENDORS = (
    "百趣", "吉凯", "拜谱", "联川", "华大", "中科", "美吉", "诺禾", "伯豪", "鲸舟",
    "迈维", "宏序", "贝纳", "里来思诺", "昊为泰", "晶世特", "奥希尔", "润泽", "普奈斯", "武汉普奈斯",
)

TENDER_HINTS = ("招标文件", "采购文件", "谈判文件", "磋商文件", "比选文件", "询比函", "需求书", "评分标准", "采购公告", "招标公告")
QUALIFICATION_HINTS = ("资格证明", "资质", "营业执照", "许可证", "社保证明", "社保凭证", "纳税", "完税", "审计报告", "中小企业声明", "信用中国")
RECEIPT_HINTS = ("银行回单", "回单", "进账", "到账", "电子回单")
PROCESS_FILE_HINTS = ("付款", "缴费", "支付凭证", "发票", "报销", "报名", "保证金", "履约", "密封", "封条", "封面", "签到", "保函", "回执")
RESPONSE_HINTS = ("响应文件", "投标文件", "商务技术文件", "商务文件", "技术文件", "报价文件",
                  "报价单", "报价表", "响应表", "技术参数配置清单", "投标报价", "分项报价")
# 分册/部分类响应词（高确定性；须先于资质词判定，否则「资格证明分册」会被资质截走）
PARTITION_HINTS = ("商务技术部分", "商务技术分册", "商务部分", "技术部分",
                   "资格文件部分", "资格证明部分", "资格证明分册", "报价部分")
FINAL_FLAGS = ("最终版", "定稿版", "定稿", "终稿", "签章版", "盖章版", "正本",
               "盖章扫描版", "盖章扫描件", "扫描盖章")
NON_FINAL_HINTS = ("待定稿", "未定稿", "待盖章", "未盖章", "待签章", "未签章", "作废", "不需要提供")
SIGNED_AUXILIARY_HINTS = ("发票", "封皮", "封面", "封条", "外包装", "盖章要求", "签章要求",
                         "盖章说明", "签章说明", "提前盖章", "现场填写")

_ORDER_NO_RE = re.compile(r"(?:BOE|YOE|ZOE|DZOE|YLM)\d{6,}")
_AMOUNT_RE = re.compile(r"(?<!\d)(?:\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2}|[5-9]\d{4}|\d{6,})(?!\d)")

UNKNOWN = "unknown"
AMBIGUOUS = "ambiguous"
OUR_RESPONSE = "our_response"
FINAL_SIGNED = "final_signed"
COMPETITOR_RESPONSE = "competitor_response"
TENDER_REQUIREMENT = "tender_requirement"
CONTRACT_EVIDENCE = "contract_evidence"
QUALIFICATION_EVIDENCE = "qualification_evidence"
PROCESS_MATERIAL = "process_material"
SYSTEM_TEMP = "system_or_temp"


@dataclass(frozen=True)
class RoleResult:
    role: str
    vendor: str | None = None
    confidence: float = 0.0
    reason: str = ""
    conflict: bool = False


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    t = text.casefold()
    return any(h.casefold() in t for h in hints)


def _vendors_in(text: str, names: tuple[str, ...]) -> list[str]:
    t = text.casefold()
    return [n for n in names if n.casefold() in t]


def _is_contract_by_order_number(filename: str) -> bool:
    m = _ORDER_NO_RE.search(filename)
    if not m:
        return False
    rest = filename[: m.start()] + filename[m.end():]
    return bool(_AMOUNT_RE.search(rest))


def _dir_vendors(rel_inside: str, names: tuple[str, ...]) -> list[str]:
    """供应商目录 =「项目内相对路径」的父目录段（全段规范化等值匹配）。
    不识别“中科没用”为“中科”；父段只认完整目录段。"""
    parts = [p for p in rel_inside.split("/") if p]
    if not parts:
        return []
    dir_parts = parts[:-1]  # 去掉文件名
    found = []
    for seg in dir_parts:
        seg_norm = re.sub(r"\s+", "", seg)
        for name in names:
            name_norm = re.sub(r"\s+", "", name)
            if seg_norm == name_norm:
                found.append(name)
    return found


def classify(project_folder: str = "", relative_path_within_project: str = "",
             *, manual_override: bool = False, project_type: str | None = None) -> RoleResult:
    if manual_override:
        return RoleResult(UNKNOWN, None, 1.0, "manual_override: 人工决定")

    rel = (relative_path_within_project or "").replace("\\", "/").strip("/")
    if not rel:
        return RoleResult(UNKNOWN, None, 0.0, "空 relative_path")

    filename = rel.split("/")[-1]
    parent = rel.rpartition("/")[0]
    is_response_filename = _contains_any(filename, RESPONSE_HINTS + PARTITION_HINTS)
    is_signed_candidate = _contains_any(filename, ("最终", "定稿", "终稿", "签章", "盖章", "正本"))
    fn_low = filename.casefold()
    dir_vendors_ours = _dir_vendors(rel, OUR_VENDORS)
    dir_vendors_comp = _dir_vendors(rel, COMP_VENDORS)

    # —— 系统/临时 ——
    if fn_low in ("thumbs.db",) or filename.startswith(("~$", ".~")):
        return RoleResult(SYSTEM_TEMP, None, 1.0, "系统文件")
    if fn_low.endswith((".db", ".exe")):
        return RoleResult(SYSTEM_TEMP, None, 1.0, "系统扩展名")

    # —— 明确文件身份 ——
    if _is_contract_by_order_number(filename):
        return RoleResult(CONTRACT_EVIDENCE, None, 0.9, "订单号+金额=合同证据")
    if fn_low.startswith("dzfp"):
        return RoleResult(PROCESS_MATERIAL, None, 0.9, "非合同电子发票")
    if _contains_any(rel, RECEIPT_HINTS):
        return RoleResult(PROCESS_MATERIAL, None, 0.9, "回单")
    if _contains_any(rel, TENDER_HINTS):
        return RoleResult(TENDER_REQUIREMENT, None, 0.9, "招标要求")
    if _contains_any(rel, QUALIFICATION_HINTS) and not _contains_any(filename, PARTITION_HINTS):
        return RoleResult(QUALIFICATION_EVIDENCE, None, 0.85, "资质/资格")
    # 附件用途先于签章状态；合同目录不能吞掉有明确响应身份的整份文件。
    if is_signed_candidate and not is_response_filename and (
        _contains_any(filename, ("合同", "业绩证明", "业绩材料"))
        or any(seg in ("合同", "合同附件", "项目业绩") for seg in parent.split("/"))
    ):
        return RoleResult(CONTRACT_EVIDENCE, None, 0.9, "独立合同/业绩附件")
    # 封皮/信封/封装件 → process_material（用户拍板：封皮仅含主体信息，非响应正文，无条件 process）
    # 放在强身份(招标/资质/合同)与签章/供应商目录之前，封皮不被“响应关键词/我方目录”误抢。
    if any(h in filename for h in ("封皮", "信封", "外包装", "封条", "封面")):
        return RoleResult(PROCESS_MATERIAL, None, 0.9, "封皮/信封/封装件")
    if is_signed_candidate and _contains_any(filename, SIGNED_AUXILIARY_HINTS):
        return RoleResult(PROCESS_MATERIAL, None, 0.9, "发票/封装/签章说明等过程附件")

    # —— 供应商冲突 → 统一 unknown + vendor_conflict ——
    fn_ours_all = _vendors_in(filename, OUR_VENDORS)
    fn_comp_all = _vendors_in(filename, COMP_VENDORS)

    # 冲突和竞品边界须先于签章规则，不能被“响应/正本”提前返回绕过。
    if ((fn_ours_all and fn_comp_all) or (dir_vendors_comp and (dir_vendors_ours or fn_ours_all))
            or (dir_vendors_ours and fn_comp_all)):
        return RoleResult(UNKNOWN, None, 0.5, "vendor_conflict: 目录/文件名的我方与竞品身份冲突", conflict=True)
    if dir_vendors_comp:
        return RoleResult(COMPETITOR_RESPONSE, dir_vendors_comp[0], 0.95, "独立竞品目录(报价表等随目录)")
    if fn_comp_all:
        return RoleResult(COMPETITOR_RESPONSE, fn_comp_all[0], 0.9, "文件名明确属竞品")

    # 签章是状态，不证明文件用途。只有我方响应身份和肯定的最终状态同时成立才升级。
    if is_signed_candidate:
        if _contains_any(filename, ("多家公司", "多家供应商", "多供应商")):
            return RoleResult(UNKNOWN, None, 0.4, "pending_review: 多主体签章材料，响应归属待确认")
        has_response_identity = bool(dir_vendors_ours) or is_response_filename or _contains_any(parent, RESPONSE_HINTS + PARTITION_HINTS)
        has_our_identity = bool(dir_vendors_ours or fn_ours_all)
        if (has_our_identity and has_response_identity and _contains_any(filename, FINAL_FLAGS)
                and not _contains_any(rel, NON_FINAL_HINTS)):
            return RoleResult(FINAL_SIGNED, "欧易生物", 0.9, "我方响应身份+最终状态；路径规则待内容核验")
        if not has_response_identity:
            return RoleResult(UNKNOWN, None, 0.4, "pending_review: 签章标记缺少响应文件身份")

    # 响应分册/部分类词：高确定性响应身份，先于资质词（避免「资格证明分册」被资质截走）
    if _contains_any(filename, PARTITION_HINTS):
        return RoleResult(OUR_RESPONSE, None, 0.85, "响应分册/部分")

    if dir_vendors_ours and not fn_comp_all:
        return RoleResult(OUR_RESPONSE, "欧易生物", 0.9, "独立我方目录")

    # —— 文件名供应商（非目录冲突时） ——
    if fn_ours_all:
        return RoleResult(OUR_RESPONSE, "欧易生物", 0.85, "文件名明确属我方")

    # —— 通用响应关键词 ——
    if _contains_any(filename, RESPONSE_HINTS):
        return RoleResult(OUR_RESPONSE, None, 0.7, "通用响应关键词(默认我方, 需人工)")

    # —— 过程/报名 ——
    if _contains_any(filename, PROCESS_FILE_HINTS):
        return RoleResult(PROCESS_MATERIAL, None, 0.7, "过程材料")

    return RoleResult(UNKNOWN, None, 0.0, "无法判定")


def confirm_our_from_content(content: str) -> bool:
    """内容确认覆盖层：仅当【首页明确投标人/供应商为 欧易/鹿明】时确证我方响应。
    路径规则够不到的低置信名（如「推介文件」）靠此层；不 OCR/ 不 LLM。
    """
    if not content:
        return False
    t = content.casefold()
    if not any(o in t for o in ("上海欧易", "欧易生物", "欧易", "鹿明")):
        return False
    return any(b in t for b in ("投标人", "投标单位", "供应商", "响应人", "响应方", "乙方", "受托方"))


# 兼容旧调用（路径即 project/rel）；迁移期间使用
def classify_path(relative_path: str) -> RoleResult:
    p = relative_path.replace("\\", "/").strip("/")
    parts = p.split("/")
    if len(parts) >= 2:
        # 旧式全部路径：首段当 project，其余当 rel
        return classify(project_folder=parts[0], relative_path_within_project="/".join(parts[1:]))
    return classify(project_folder="", relative_path_within_project=p)
