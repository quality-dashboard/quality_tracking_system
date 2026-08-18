"""
backfill_defects.py v3 - 缺陷字段重新清洗与回填（报废+返修双源版）
舍弗勒银川工厂 - 车间质量检测跟踪系统

【v3 修复】
  1. 报废原因: 全部统计, 不跳过"返修"字样
  2. 返修原因: 含"已修"整条排除, 不含则统计
  3. 空值保持空字符串"", 不补0 (图表显示空白而非0折线)
  4. 同字段累加 (报废原因提取的 + 返修原因提取的)

用法:
  python backfill_defects.py --dry-run            # 只打印计划, 不动数据库
  python backfill_defects.py --execute            # 真实回填
  python backfill_defects.py --rollback <备份CSV>  # 从备份回滚
"""
import sys
import os
import re
import argparse
import pandas as pd
from datetime import datetime

# ============================================================
# 1. 读取 secrets.toml (绕过 st.secrets)
# ============================================================
SECRETS_PATH = r"D:\quality_tracking_system\.streamlit\secrets.toml"
BACKUP_DIR = r"D:\quality_tracking_system\backups"

secrets = {}
with open(SECRETS_PATH, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$', line)
        if m:
            key = m.group(1)
            val = m.group(2).strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            secrets[key] = val

import streamlit as st
class _FakeSecrets:
    def __getitem__(self, key):
        return secrets[key]
    def get(self, key, default=None):
        return secrets.get(key, default)
st.secrets = _FakeSecrets()

sys.path.insert(0, r"D:\quality_tracking_system")
from data_utils import load_all_records, _execute

# ============================================================
# 2. 字段映射 (7原字段 + 5新增字段)
# ============================================================
DISPLAY_FIELDS = ["锻造缺陷", "磕伤/卡伤", "接刀痕", "过车/欠车", "油沟", "倒角",
                  "椭圆超差", "内径垫伤", "滚道对称点过大", "对称度超差", "幅高椭圆"]
DB_FIELDS = ["锻造缺陷", "磕伤卡伤", "接刀痕", "过车欠车", "油沟", "倒角",
             "椭圆超差", "内径垫伤", "滚道对称点过大", "对称度超差", "幅高椭圆"]
DISPLAY_TO_DB = dict(zip(DISPLAY_FIELDS, DB_FIELDS))
LEAKBLACK_DB_FIELD = "漏黑"

KEYWORD_RULES = [
    ("过车/欠车", ["过车", "欠车"]),
    ("磕伤/卡伤", ["磕伤", "卡伤"]),
    ("接刀痕",   ["接刀痕", "接刀", "车刀痕"]),  # ★ v3.1: 新增"车刀痕"
    ("倒角",     ["倒角"]),
    ("油沟",     ["油沟"]),
    ("锻造缺陷", ["锻造", "锻件", "锻打"]),
    # ★ v4 新增5个缺陷类型 (关键词用完整词, 与文本一致)
    ("滚道对称点过大", ["滚道对称点过大"]),
    ("对称度超差", ["对称度超差"]),
    ("椭圆超差", ["椭圆超差"]),
    ("内径垫伤", ["内径垫伤"]),
    ("幅高椭圆", ["幅高椭圆"]),
]

LEAKBLACK_KEYWORD = "漏黑"

# ★ v4: "已修"排除规则扩展, 含"已返修"也排除 (避免"11件已返修"被误录为缺陷)
EXCLUDE_KEYWORDS = ["已修", "已返修"]


# ============================================================
# 3. 语义拆分核心函数
# ============================================================
def match_keyword(text):
    """匹配单个片段里的缺陷关键词, 返回显示字段名或None"""
    for field, keywords in KEYWORD_RULES:
        for kw in keywords:
            if kw in text:
                return field
    return None


def extract_qty(part):
    """从片段提取数量, 无数量返回1. 支持阿拉伯数字和中文数字"""
    # 阿拉伯数字: "2件"
    m = re.search(r'(\d+)\s*件', part)
    if m:
        return int(m.group(1))
    # 中文数字
    cn_map = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    m = re.search(r'(一|二|两|三|四|五|六|七|八|九|十)\s*件', part)
    if m:
        return cn_map.get(m.group(1), 1)
    return 1


def parse_segments(text):
    """
    把文本拆成 [(field, qty), ...]
    field: 6个显示字段之一 或 "漏黑"
    qty: 整型数量
    返回空列表表示无法解析
    """
    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    text = text.strip()
    if not text:
        return []

    segments = []

    # ★ 处理"各X件"结构: "过车、磕伤各1件" -> 过车1, 磕伤1
    m_each = re.search(r'各\s*(\d+)\s*件', text)
    if m_each:
        qty = int(m_each.group(1))
        prefix = text[:m_each.start()]
        parts = re.split(r'[、,，;；\s]+', prefix)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if LEAKBLACK_KEYWORD in part:
                segments.append(("漏黑", qty))
            else:
                field = match_keyword(part)
                if field:
                    segments.append((field, qty))
        return segments

    # ★ 普通分片: 按顿号/逗号/分号/换行分隔
    parts = re.split(r'[、,，;；\n]+', text)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 漏黑优先判断
        if LEAKBLACK_KEYWORD in part:
            segments.append(("漏黑", extract_qty(part)))
            continue
        # 6字段匹配
        field = match_keyword(part)
        if field:
            segments.append((field, extract_qty(part)))

    return segments


def parse_scrap_reason(text):
    """报废原因解析: 全部统计, 不跳过任何字样"""
    return parse_segments(text)


def parse_rework_reason(text):
    """
    返修原因解析 (v3.1 分片排除版):
    - 分片处理, 含"已修"的片段排除, 其余片段正常统计
    - 返回 (segments, excluded_parts)
      segments: [(field, qty), ...] 统计的片段
      excluded_parts: [片段文本, ...] 被排除的片段
    """
    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    text = text.strip()
    if not text:
        return [], []

    segments = []
    excluded_parts = []

    # ★ 处理"各X件"结构
    m_each = re.search(r'各\s*(\d+)\s*件', text)
    if m_each:
        qty = int(m_each.group(1))
        prefix = text[:m_each.start()]
        parts = re.split(r'[、,，;；\s]+', prefix)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            # ★ v4: 含已修/已返修的片段跳过 (扩展排除规则)
            if any(kw in part for kw in EXCLUDE_KEYWORDS):
                excluded_parts.append(part)
                continue
            if LEAKBLACK_KEYWORD in part:
                segments.append(("漏黑", qty))
            else:
                field = match_keyword(part)
                if field:
                    segments.append((field, qty))
        return segments, excluded_parts

    # ★ 普通分片: 按顿号/逗号/分号/换行分隔
    parts = re.split(r'[、,，;；\n]+', text)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # ★ v4: 含已修/已返修的片段跳过 (分片排除, 不整条排除)
        if any(kw in part for kw in EXCLUDE_KEYWORDS):
            excluded_parts.append(part)
            continue
        # 漏黑优先判断
        if LEAKBLACK_KEYWORD in part:
            segments.append(("漏黑", extract_qty(part)))
            continue
        # 6字段匹配
        field = match_keyword(part)
        if field:
            segments.append((field, extract_qty(part)))

    return segments, excluded_parts


# ============================================================
# 4. 备份与回滚
# ============================================================
def backup_records(df):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, "backup_v3_{}.csv".format(ts))
    df.to_csv(backup_path, index=False, encoding="utf-8-sig")
    print("[备份] 已保存到: {}".format(backup_path))
    return backup_path


def rollback_from_backup(backup_path):
    if not os.path.exists(backup_path):
        print("[错误] 备份文件不存在: {}".format(backup_path))
        return False
    df = pd.read_csv(backup_path, encoding="utf-8-sig")
    print("[回滚] 从备份读取 {} 条记录".format(len(df)))

    ensure_leakblack_column()

    success = 0
    fail = 0
    all_fields = DISPLAY_FIELDS + ["漏黑"]
    for _, row in df.iterrows():
        try:
            record_id = int(row["id"])
            set_clauses = []
            args = []
            for field in all_fields:
                db_field = DISPLAY_TO_DB.get(field, field)
                val = row.get(field, "")
                if pd.isna(val):
                    val = ""
                else:
                    val = str(val)
                set_clauses.append('"{}" = ?'.format(db_field))
                args.append(val)
            args.append(record_id)
            sql = 'UPDATE quality_records SET {} WHERE id = ?'.format(", ".join(set_clauses))
            _execute(sql, args)
            success += 1
        except Exception as e:
            print("  [失败] id={} 错误: {}".format(row.get("id", "?"), e))
            fail += 1

    print("[回滚] 完成: 成功 {} 条, 失败 {} 条".format(success, fail))
    return True


def ensure_leakblack_column():
    """确保数据库有漏黑列, 没有则ALTER TABLE新增"""
    try:
        _execute('SELECT "漏黑" FROM quality_records LIMIT 1')
    except Exception:
        print("[表结构] 漏黑列不存在, 执行ALTER TABLE新增...")
        try:
            _execute('ALTER TABLE quality_records ADD COLUMN "漏黑" TEXT DEFAULT ""')
            print("[表结构] 漏黑列新增成功")
        except Exception as e:
            print("[表结构] ALTER TABLE失败: {}".format(e))
            raise


def ensure_new_columns():
    """v4: 确保数据库有5个新缺陷列(椭圆超差/内径垫伤/滚道对称点过大/对称度超差/幅高椭圆), 缺失则ALTER TABLE"""
    new_fields = ["椭圆超差", "内径垫伤", "滚道对称点过大", "对称度超差", "幅高椭圆"]
    for col in new_fields:
        try:
            _execute('SELECT "{}" FROM quality_records LIMIT 1'.format(col))
        except Exception:
            print("[表结构] {}列不存在, 执行ALTER TABLE新增...".format(col))
            try:
                _execute('ALTER TABLE quality_records ADD COLUMN "{}" TEXT DEFAULT ""'.format(col))
                print("[表结构] {}列新增成功".format(col))
            except Exception as e:
                print("[表结构] {} ALTER TABLE失败: {}".format(col, e))
                raise


# ============================================================
# 5. 回填主逻辑
# ============================================================
def plan_backfill(df):
    """
    生成回填计划
    返回: {
        'fill_updates': [(id, field_display, value, source), ...],
        'multi_defect_samples': [(id, source, text, segments), ...],
        'rework_excluded': [(id, text), ...],  # 返修原因含已修被排除的
        'rework_included': [(id, text, segments), ...],  # 返修原因统计的
        'unmatched': [(id, model, date, source, text), ...],
    }
    """
    fill_updates = []
    multi_defect_samples = []
    rework_excluded = []
    rework_included = []
    unmatched = []

    for idx, row in df.iterrows():
        record_id = row.get("id")
        if pd.isna(record_id):
            continue
        record_id = int(record_id)
        model = row.get("型号", "")
        date = row.get("日期", "")

        # ===== 报废原因处理 =====
        scrap_reason = row.get("报废原因", "")
        if pd.isna(scrap_reason):
            scrap_reason = ""
        else:
            scrap_reason = str(scrap_reason).strip()

        if scrap_reason:
            segments = parse_scrap_reason(scrap_reason)
            if segments:
                if len(segments) >= 2:
                    multi_defect_samples.append((record_id, "报废原因", scrap_reason, segments))
                # 累加到该记录
                field_qty = {}
                for field, qty in segments:
                    field_qty[field] = field_qty.get(field, 0) + qty
                for field, qty in field_qty.items():
                    fill_updates.append((record_id, field, str(qty), "报废原因"))
            else:
                # 无法解析
                unmatched.append((record_id, model, date, "报废原因", scrap_reason))

        # ===== 返修原因处理 (v3.1: 分片排除含已修的片段) =====
        rework_reason = row.get("返修原因", "")
        if pd.isna(rework_reason):
            rework_reason = ""
        else:
            rework_reason = str(rework_reason).strip()

        if rework_reason:
            # ★ 分片排除: 含已修的片段排除, 其余统计
            segments, excluded_parts = parse_rework_reason(rework_reason)
            if excluded_parts:
                rework_excluded.append((record_id, rework_reason, excluded_parts))
            if segments:
                if len(segments) >= 2:
                    multi_defect_samples.append((record_id, "返修原因", rework_reason, segments))
                rework_included.append((record_id, rework_reason, segments))
                # 累加到该记录 (与报废原因的累加)
                existing = {}
                for i, (rid, field, val, src) in enumerate(fill_updates):
                    if rid == record_id:
                        existing[field] = int(val)
                field_qty = existing.copy()
                for field, qty in segments:
                    field_qty[field] = field_qty.get(field, 0) + qty
                # 更新fill_updates: 移除该记录旧的, 重新加
                fill_updates = [u for u in fill_updates if u[0] != record_id]
                for field, qty in field_qty.items():
                    fill_updates.append((record_id, field, str(qty), "报废+返修"))
            elif not excluded_parts:
                # 无segments也无排除片段 = 无法解析
                unmatched.append((record_id, model, date, "返修原因", rework_reason))

    return {
        "fill_updates": fill_updates,
        "multi_defect_samples": multi_defect_samples,
        "rework_excluded": rework_excluded,
        "rework_included": rework_included,
        "unmatched": unmatched,
    }


def execute_clear_and_fill(df, plan, dry_run=True):
    """
    执行: ① 清空所有记录的6字段+漏黑为空字符串"" ② 回填解析出的数据
    空值保持空, 不补0
    """
    # 按 id 聚合回填值
    fill_by_id = {}
    for rid, field, val, source in plan["fill_updates"]:
        fill_by_id.setdefault(rid, {})[field] = val

    if dry_run:
        print("\n[DRY-RUN] 计划操作:")
        print("  ① 清空: 全部 {} 条记录的6字段+漏黑 = 空字符串".format(len(df)))
        print("  ② 回填: {} 条记录有解析数据".format(len(fill_by_id)))
        return 0, 0

    success = 0
    fail = 0
    all_fields = DISPLAY_FIELDS + ["漏黑"]
    for _, row in df.iterrows():
        try:
            record_id = int(row["id"])
            # 该记录的回填值 (默认全空)
            field_vals = {f: "" for f in all_fields}
            if record_id in fill_by_id:
                field_vals.update(fill_by_id[record_id])

            set_clauses = []
            args = []
            for field in all_fields:
                db_field = DISPLAY_TO_DB.get(field, field)
                set_clauses.append('"{}" = ?'.format(db_field))
                args.append(field_vals[field])
            args.append(record_id)
            sql = 'UPDATE quality_records SET {} WHERE id = ?'.format(", ".join(set_clauses))
            _execute(sql, args)
            success += 1
        except Exception as e:
            print("  [失败] id={} 错误: {}".format(row.get("id", "?"), e))
            fail += 1
    return success, fail


# ============================================================
# 6. 验证报告
# ============================================================
def print_validation_report(df, plan):
    """输出验证报告"""
    print("\n" + "=" * 70)
    print("【验证报告】")
    print("=" * 70)

    # 模拟回填后状态
    df_sim = df.copy()
    for field in DISPLAY_FIELDS + ["漏黑"]:
        if field not in df_sim.columns:
            df_sim[field] = ""
    # 清空为空字符串
    for field in DISPLAY_FIELDS + ["漏黑"]:
        df_sim[field] = ""
    # 应用回填
    fill_map = {}
    for rid, field, val, source in plan["fill_updates"]:
        fill_map[(rid, field)] = val
    for (rid, field), val in fill_map.items():
        mask = df_sim["id"] == rid
        df_sim.loc[mask, field] = val

    # 1. 各缺陷类型总数量
    print("\n--- 1. 回填后各缺陷类型分布 ---")
    print("{:<12} | {:<10} | {:<10}".format("缺陷类型", "非空记录数", "数量总和"))
    print("-" * 40)
    for field in DISPLAY_FIELDS + ["漏黑"]:
        cnt = 0
        total = 0
        for v in df_sim[field]:
            s = str(v).strip()
            if s == "":
                continue
            try:
                num = int(s)
                if num > 0:
                    cnt += 1
                    total += num
            except (ValueError, TypeError):
                pass
        print("{:<12} | {:<10} | {:<10}".format(field, cnt, total))

    # 2. 多缺陷拆解样例 (前10条)
    print("\n--- 2. 多缺陷记录拆解验证 (前10条) ---")
    for i, (rid, source, text, segments) in enumerate(plan["multi_defect_samples"][:10]):
        print("\n  id={} [{}]: '{}'".format(rid, source, text))
        print("  拆解结果:")
        for field, qty in segments:
            print("    -> {} = {}".format(field, qty))

    # 3. 返修原因带"已修"分片排除验证
    print("\n--- 3. 返修原因'已修'分片排除验证 ---")
    if plan["rework_excluded"]:
        print("含'已修'片段被排除的记录 ({}条):".format(len(plan["rework_excluded"])))
        for rid, text, excluded_parts in plan["rework_excluded"]:
            print("  id={}: '{}'".format(rid, text.replace("\n", " | ")))
            print("    排除片段: {}".format(excluded_parts))
    else:
        print("  无含'已修'片段的返修原因")

    # 4. 返修原因统计样例 (前5条)
    print("\n--- 4. 返修原因统计样例 (前5条) ---")
    for rid, text, segments in plan["rework_included"][:5]:
        print("  id={}: '{}'".format(rid, text.replace("\n", " | ")))
        for field, qty in segments:
            print("    -> {} = {}".format(field, qty))

    # 5. 无法处理
    print("\n--- 5. 无法解析的记录 ---")
    print("  共 {} 条".format(len(plan["unmatched"])))
    if plan["unmatched"]:
        print("  明细 (前10条):")
        for rid, model, date, source, text in plan["unmatched"][:10]:
            print("    id={} {} {} [{}]: '{}'".format(rid, date, model, source, text[:40]))


# ============================================================
# 7. 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="缺陷字段重新清洗与回填 v3")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划, 不动数据库")
    parser.add_argument("--execute", action="store_true", help="真实回填")
    parser.add_argument("--rollback", metavar="BACKUP_CSV", help="从备份回滚")
    parser.add_argument("--backup", metavar="BACKUP_CSV", help="复用已有备份")
    args = parser.parse_args()

    if args.rollback:
        print("=" * 70)
        print("【回滚模式】从备份恢复6个缺陷字段+漏黑")
        print("=" * 70)
        rollback_from_backup(args.rollback)
        return

    if not args.execute:
        args.dry_run = True

    print("=" * 70)
    print("【缺陷字段重新清洗与回填 v3】" + ("[DRY-RUN]" if args.dry_run else "[EXECUTE]"))
    print("  规则: 报废原因全统计 + 返修原因排已修 + 空值保持空")
    print("=" * 70)

    # 1. 加载数据
    print("\n[1/6] 加载全部记录...")
    df = load_all_records()
    print("  共 {} 条记录".format(len(df)))

    # 2. 确保漏黑列 + 5个新缺陷列
    if not args.dry_run:
        print("\n[2/6] 检查表结构...")
        ensure_leakblack_column()
        ensure_new_columns()
    else:
        print("\n[2/6] DRY-RUN跳过表结构检查")

    # 3. 备份
    print("\n[3/6] 备份当前数据...")
    if args.backup:
        if not os.path.exists(args.backup):
            print("[错误] 指定备份不存在: {}".format(args.backup))
            return
        backup_path = args.backup
        print("[备份] 复用: {}".format(backup_path))
    else:
        backup_path = backup_records(df)

    # 4. 生成回填计划
    print("\n[4/6] 解析报废原因+返修原因文本...")
    plan = plan_backfill(df)
    print("  回填操作: {} 条字段更新 (来自 {} 条记录)".format(
        len(plan["fill_updates"]),
        len(set(u[0] for u in plan["fill_updates"]))
    ))
    print("  多缺陷记录: {} 条".format(len(plan["multi_defect_samples"])))
    print("  返修原因含已修排除: {} 条".format(len(plan["rework_excluded"])))
    print("  返修原因统计: {} 条".format(len(plan["rework_included"])))
    print("  无法解析: {} 条".format(len(plan["unmatched"])))

    # 5. 验证报告
    print_validation_report(df, plan)

    # 6. 执行
    print("\n[5/6] " + ("展示计划(DRY-RUN)" if args.dry_run else "执行清空+回填..."))
    success, fail = execute_clear_and_fill(df, plan, dry_run=args.dry_run)
    if not args.dry_run:
        print("\n[6/6] 执行完成: 成功 {} 条, 失败 {} 条".format(success, fail))
        print("\n备份文件: {}".format(backup_path))
        print("如需回滚: python backfill_defects.py --rollback {}".format(backup_path))
    else:
        print("\n[6/6] DRY-RUN完成, 未修改数据库")
        print("\n确认拆解结果无误后, 运行: python backfill_defects.py --execute")

    print("\n" + "=" * 70)
    print("处理完成")
    print("=" * 70)


if __name__ == "__main__":
    main()
