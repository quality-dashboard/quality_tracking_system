import json as _json
import urllib.request
import pandas as pd
import streamlit as st
from typing import List, Dict, Any, Optional

from config import (
    WARNING_THRESHOLD,
    CONSECUTIVE_DAYS,
    SCRAP_COUNT_WARNING,
    SCRAP_COUNT_CRITICAL,
)

# ============================================================
# ★★★ Turso 数据库连接配置区（请填写你的真实 token） ★★★
# ============================================================
TURSO_URL ="libsql://qualitytracking-quality-dashboard.turso.io"
TURSO_AUTH_TOKEN =  st.secrets["TURSO_TOKEN"]
# ============================================================

BASE_COLUMNS = [
    "日期", "班次", "型号", "产品类型", "订单号", "车削操作者", "工序",
    "检测数量", "合格数量", "返修数量", "报废数量",
    "报废率", "返修率", "总不合格率",
    "报废原因", "返修原因",
]

# 注意: 数据库列名不带斜杠, 避免 Turso SQL 解析器报 400
# 7个原字段 + 5个新增字段(椭圆超差/内径垫伤/滚道对称点过大/对称度超差/幅高椭圆)
DEFECT_COLUMNS = ["锻造缺陷", "磕伤卡伤", "接刀痕", "过车欠车", "油沟", "倒角", "漏黑",
                  "椭圆超差", "内径垫伤", "滚道对称点过大", "对称度超差", "幅高椭圆"]

ALL_COLUMNS = BASE_COLUMNS + DEFECT_COLUMNS

# 界面显示名 <-> 数据库列名 的映射 (新5字段无斜杠, 显示名=数据库名, 无需映射)
_DISPLAY_TO_DB = {"磕伤/卡伤": "磕伤卡伤", "过车/欠车": "过车欠车"}
_DB_TO_DISPLAY = {"磕伤卡伤": "磕伤/卡伤", "过车欠车": "过车/欠车"}

# 界面显示名 (load_all_records 返回的列名, 带斜杠的带斜杠, 新5字段不带斜杠)
DEFECT_FIELDS_DISPLAY = ["锻造缺陷", "磕伤/卡伤", "接刀痕", "过车/欠车", "油沟", "倒角", "漏黑",
                         "椭圆超差", "内径垫伤", "滚道对称点过大", "对称度超差", "幅高椭圆"]


class _Result:
    def __init__(self, columns, rows, affected_row_count):
        self.columns = columns
        self.rows = rows
        self.affected_row_count = affected_row_count


def _get_http_url():
    url = TURSO_URL
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    if url.endswith("/"):
        url = url[:-1]
    return url + "/v2/pipeline"


def _to_turso_arg(value):
    if value is None:
        return {"type": "null"}
    # bool 必须在 int 之前判断 (Python 中 True 也是 int)
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}  # 字符串
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}  # 字符串
    if isinstance(value, float):
        return {"type": "float", "value": value}  # 数字
    return {"type": "text", "value": str(value)}  # 字符串

def _from_turso_value(val):
    if val is None:
        return None
    t = val.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(val.get("value", 0))
    if t == "float":
        return float(val.get("value", 0.0))
    if t == "text":
        return val.get("value", "")
    return val.get("value")


def _parse_result(result_json):
    results = result_json.get("results", [])
    if not results:
        return _Result([], [], 0)
    first = results[0]
    if first.get("type") == "error":
        err = first.get("error", {})
        msg = err.get("message", "未知错误")
        raise RuntimeError("Turso错误: " + msg)
    response = first.get("response", {})
    if response.get("type") != "execute":
        return _Result([], [], 0)
    res = response.get("result", {})
    cols = [c.get("name", "") for c in res.get("cols", [])]
    raw_rows = res.get("rows", [])
    rows = [[_from_turso_value(v) for v in r] for r in raw_rows]
    affected = res.get("affected_row_count", 0)
    return _Result(cols, rows, affected)


def _execute(sql, args=None):
    turso_args = [_to_turso_arg(a) for a in args] if args else []
    body = {
        "requests": [
            {"type": "execute", "stmt": {"sql": sql, "args": turso_args}},
            {"type": "close"},
        ]
    }
    data = _json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        _get_http_url(),
        data=data,
        headers={
            "Authorization": "Bearer " + TURSO_AUTH_TOKEN,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = _json.loads(resp.read().decode("utf-8"))
    return _parse_result(result)


def init_db():
    """初始化数据库表结构"""
    cols_sql = ",\n".join(['"{}" TEXT DEFAULT ""'.format(c) for c in DEFECT_COLUMNS])
    create_sql = """
        CREATE TABLE IF NOT EXISTS quality_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            日期 TEXT NOT NULL,
            班次 TEXT NOT NULL,
            型号 TEXT NOT NULL,
            产品类型 TEXT DEFAULT "",
            订单号 TEXT DEFAULT "",
            车削操作者 TEXT DEFAULT "",
            工序 TEXT NOT NULL,
            检测数量 INTEGER DEFAULT 0,
            合格数量 INTEGER DEFAULT 0,
            返修数量 INTEGER DEFAULT 0,
            报废数量 INTEGER DEFAULT 0,
            报废率 REAL DEFAULT 0.0,
            返修率 REAL DEFAULT 0.0,
            总不合格率 REAL DEFAULT 0.0,
            报废原因 TEXT DEFAULT "",
            返修原因 TEXT DEFAULT "",
            {}
        )
    """.format(cols_sql)
    _execute(create_sql)
    # 幂等补充缺失的缺陷列 (兼容已有数据库: 新增字段时自动ALTER TABLE)
    _ensure_defect_columns()


def _ensure_defect_columns():
    """检查DEFECT_COLUMNS里的列是否都存在, 缺失的ALTER TABLE补充"""
    for col in DEFECT_COLUMNS:
        try:
            _execute('SELECT "{}" FROM quality_records LIMIT 1'.format(col))
        except Exception:
            try:
                _execute('ALTER TABLE quality_records ADD COLUMN "{}" TEXT DEFAULT ""'.format(col))
                print("[表结构] 新增列: {}".format(col))
            except Exception as e:
                print("[表结构] ALTER TABLE失败 {}: {}".format(col, e))


def save_record(record):
    """保存单条检测记录, 自动把界面字段名转为数据库列名"""
    try:
        clean_record = {}
        for k, v in record.items():
            k = _DISPLAY_TO_DB.get(k, k)
            clean_record[k] = v

        values = [clean_record.get(c, "") for c in ALL_COLUMNS]
        col_names = ", ".join(['"{}"'.format(c) for c in ALL_COLUMNS])
        placeholders = ", ".join(["?"] * len(ALL_COLUMNS))
        sql = "INSERT INTO quality_records ({}) VALUES ({})".format(col_names, placeholders)

        _execute(sql, values)
        return True
    except Exception as e:
        print("save_record error:", e)
        return False


def save_records_batch(records):
    """批量保存记录, 返回成功条数"""
    if not records:
        return 0
    count = 0
    for r in records:
        if save_record(r):
            count += 1
    return count


def load_all_records():
    """加载全部记录为DataFrame, 自动把数据库列名转回界面显示名"""
    try:
        result = _execute("SELECT * FROM quality_records ORDER BY id DESC")
        df = pd.DataFrame(result.rows, columns=result.columns)
        df = df.rename(columns=_DB_TO_DISPLAY)
        return df
    except Exception as e:
        print("load_all_records error:", e)
        return pd.DataFrame()


def delete_record(record_id):
    """删除指定ID的记录"""
    try:
        result = _execute("DELETE FROM quality_records WHERE id = ?", [record_id])
        return result.affected_row_count > 0
    except Exception as e:
        print("delete_record error:", e)
        return False


def calculate_scrap_rate(scrap_qty, inspect_qty):
    """计算报废率(%)"""
    if inspect_qty <= 0:
        return 0.0
    return round(scrap_qty / inspect_qty * 100, 2)


def calculate_rework_rate(rework_qty, inspect_qty):
    """计算返修率(%)"""
    if inspect_qty <= 0:
        return 0.0
    return round(rework_qty / inspect_qty * 100, 2)


def calculate_total_defect_rate(scrap_rate, rework_rate):
    """计算总不合格率(%) = 报废率 + 返修率"""
    return round(scrap_rate + rework_rate, 2)


def evaluate_warning(scrap_rate, consecutive_days):
    """评估是否触发预警"""
    if scrap_rate >= WARNING_THRESHOLD and consecutive_days >= CONSECUTIVE_DAYS:
        return "连续{}天报废率≥{}%".format(consecutive_days, WARNING_THRESHOLD)
    return None


def get_alerts_from_data(df):
    """从DataFrame中提取活跃预警列表"""
    alerts = []
    if df.empty:
        return alerts
    grouped = df.groupby(["型号", "工序"]).agg({"报废数量": "sum"}).reset_index()
    for _, row in grouped.iterrows():
        scrap_count = int(row["报废数量"])
        if scrap_count >= SCRAP_COUNT_CRITICAL:
            alerts.append({
                "型号": row["型号"],
                "工序": row["工序"],
                "等级": "批次不良",
                "当日报废件数": scrap_count,
                "原因": "当日报废≥{}件, 判定小批次报废".format(SCRAP_COUNT_CRITICAL),
            })
        elif scrap_count >= SCRAP_COUNT_WARNING:
            alerts.append({
                "型号": row["型号"],
                "工序": row["工序"],
                "等级": "重点关注",
                "当日报废件数": scrap_count,
                "原因": "当日报废≥{}件, 需加强巡检".format(SCRAP_COUNT_WARNING),
            })
    return alerts


def export_report_to_excel(df, start_date: str, end_date: str) -> bytes:
    import io

    # 去掉系统字段，只保留业务字段
    drop_cols = ["id", "日期_dt"]
    for col in drop_cols:
        if col in df.columns:
            df = df.drop(columns=[col])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="质量报告")
    output.seek(0)
    return output.getvalue()

# 兼容旧导入名 (app.py 或其他模块可能引用)
DEFECT_TYPE_FIELDS = DEFECT_COLUMNS


# ============================================================
# ★★★ 以下为 v3.3 新增数据分析聚合函数 ★★★
# 所有函数接收 load_all_records() 返回的 DataFrame (列名为界面显示名, 带斜杠)
# 不修改任何现有函数, 仅在末尾新增
# ============================================================


def aggregate_model_pass_rate(df):
    """
    按型号聚合合格率
    返回 DataFrame: [型号, 检测数量, 合格数量, 报废数量, 合格率]
    合格率 = 合格数量 / 检测数量 * 100, 按合格率从高到低排列
    """
    if df.empty:
        return pd.DataFrame(columns=["型号", "检测数量", "合格数量", "报废数量", "合格率"])
    grouped = df.groupby("型号").agg(
        检测数量=("检测数量", "sum"),
        合格数量=("合格数量", "sum"),
        报废数量=("报废数量", "sum"),
    ).reset_index()
    grouped["合格率"] = grouped.apply(
        lambda r: round(r["合格数量"] / r["检测数量"] * 100, 2) if r["检测数量"] > 0 else 0.0,
        axis=1,
    )
    return grouped.sort_values("合格率", ascending=False).reset_index(drop=True)


def aggregate_defect_summary(df):
    """
    按6个缺陷字段聚合不良数据
    返回 DataFrame: [缺陷类型, 不良数量, 占比, 涉及型号数, 发生次数]
    - 不良数量: 该缺陷字段非空时, 对应记录的报废数量之和
    - 涉及型号数: 该缺陷出现的不同型号数量
    - 发生次数: 该缺陷字段非空的记录条数
    """
    if df.empty:
        return pd.DataFrame(columns=["缺陷类型", "不良数量", "占比", "涉及型号数", "发生次数"])
    rows = []
    for field in DEFECT_FIELDS_DISPLAY:
        if field not in df.columns:
            continue
        mask = df[field].astype(str).str.strip() != ""
        sub = df[mask]
        if sub.empty:
            continue
        bad_qty = int(sub["报废数量"].sum())
        model_count = int(sub["型号"].nunique())
        occur_count = len(sub)
        rows.append({
            "缺陷类型": field,
            "不良数量": bad_qty,
            "涉及型号数": model_count,
            "发生次数": occur_count,
        })
    if not rows:
        return pd.DataFrame(columns=["缺陷类型", "不良数量", "占比", "涉及型号数", "发生次数"])
    result = pd.DataFrame(rows).sort_values("不良数量", ascending=False).reset_index(drop=True)
    total = result["不良数量"].sum()
    result["占比"] = result["不良数量"].apply(lambda x: round(x / total * 100, 1) if total > 0 else 0.0)
    return result


def aggregate_top5_alerts(df, days, threshold):
    """
    近N天报废Top5型号聚合
    - days: 近多少天 (3/5/7/30)
    - threshold: 报废件数阈值
    返回 DataFrame: [型号, 检测数量, 报废数量, 报废率, 主要缺陷, 主要缺陷数量]
    """
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["日期_dt"] = pd.to_datetime(df["日期"], errors="coerce")
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
    recent = df[df["日期_dt"] >= cutoff].copy()
    if recent.empty:
        return pd.DataFrame()
    grouped = recent.groupby("型号").agg(
        检测数量=("检测数量", "sum"),
        报废数量=("报废数量", "sum"),
    ).reset_index()
    grouped = grouped[grouped["报废数量"] >= threshold]
    if grouped.empty:
        return pd.DataFrame()
    grouped["报废率"] = grouped.apply(
        lambda r: round(r["报废数量"] / r["检测数量"] * 100, 2) if r["检测数量"] > 0 else 0.0,
        axis=1,
    )
    # 找每型号主要缺陷
    main_defects = []
    main_defect_qty = []
    for model in grouped["型号"]:
        sub = recent[recent["型号"] == model]
        defect_qty = {}
        for field in DEFECT_FIELDS_DISPLAY:
            if field not in sub.columns:
                continue
            mask = sub[field].astype(str).str.strip() != ""
            q = int(sub.loc[mask, "报废数量"].sum())
            if q > 0:
                defect_qty[field] = q
        if defect_qty:
            top_defect = max(defect_qty, key=defect_qty.get)
            main_defects.append(top_defect)
            main_defect_qty.append(defect_qty[top_defect])
        else:
            main_defects.append("-")
            main_defect_qty.append(0)
    grouped["主要缺陷"] = main_defects
    grouped["主要缺陷数量"] = main_defect_qty
    return grouped.sort_values("报废数量", ascending=False).head(5).reset_index(drop=True)


def aggregate_top5_defect_by_model(df, top_n=5):
    """
    Top5型号按缺陷类型堆叠聚合 (用于图3堆叠柱状图)
    返回 DataFrame: [型号, 缺陷类型, 报废数量]
    """
    if df.empty:
        return pd.DataFrame()
    rows = []
    top5 = aggregate_top5_alerts(df, days=30, threshold=0)
    if top5.empty:
        return pd.DataFrame()
    top_models = top5["型号"].tolist()
    for model in top_models:
        sub = df[df["型号"] == model]
        for field in DEFECT_FIELDS_DISPLAY:
            if field not in sub.columns:
                continue
            mask = sub[field].astype(str).str.strip() != ""
            q = int(sub.loc[mask, "报废数量"].sum())
            if q > 0:
                rows.append({"型号": model, "缺陷类型": field, "报废数量": q})
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def aggregate_monthly_trend(df, model, defect_type):
    """
    月度趋势聚合
    - model: "全部" 或具体型号
    - defect_type: "全部" 或具体缺陷字段名(界面显示名)
    返回 DataFrame: [月份, 缺陷类型, 不良数量]
    """
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["日期_dt"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期_dt"])
    df["月份"] = df["日期_dt"].dt.strftime("%Y-%m")
    if model != "全部":
        df = df[df["型号"] == model]
    if df.empty:
        return pd.DataFrame()
    if defect_type == "全部":
        # ★ 修复: 保留6种缺陷类型, 即使数据为0也保留 (之前会跳过不存在的字段)
        all_months = sorted(df["月份"].unique())
        rows = []
        for field in DEFECT_FIELDS_DISPLAY:
            if field in df.columns:
                mask = df[field].astype(str).str.strip() != ""
                sub = df[mask]
                if not sub.empty:
                    monthly = sub.groupby("月份")["报废数量"].sum().reindex(all_months, fill_value=0).reset_index()
                else:
                    monthly = pd.DataFrame({"月份": all_months, "报废数量": [0] * len(all_months)})
            else:
                # 字段不存在, 全0
                monthly = pd.DataFrame({"月份": all_months, "报废数量": [0] * len(all_months)})
            monthly["缺陷类型"] = field
            monthly = monthly.rename(columns={"报废数量": "不良数量"})
            rows.append(monthly[["月份", "缺陷类型", "不良数量"]])
        if not rows:
            return pd.DataFrame()
        return pd.concat(rows, ignore_index=True).sort_values(["月份", "不良数量"], ascending=[True, False])
    else:
        if defect_type not in df.columns:
            return pd.DataFrame()
        mask = df[defect_type].astype(str).str.strip() != ""
        monthly = df[mask].groupby("月份")["报废数量"].sum().reset_index()
        monthly["缺陷类型"] = defect_type
        return monthly.rename(columns={"报废数量": "不良数量"})


def get_monthly_overview(df, year_month):
    """
    月度概览KPI
    - year_month: "YYYY-MM" 格式
    返回 dict: {总检验数, 合格率, 报废率, 预警次数}
    """
    if df.empty:
        return {"总检验数": 0, "合格率": 0.0, "报废率": 0.0, "预警次数": 0}
    df = df.copy()
    df["日期_dt"] = pd.to_datetime(df["日期"], errors="coerce")
    df["月份"] = df["日期_dt"].dt.strftime("%Y-%m")
    month_df = df[df["月份"] == year_month]
    if month_df.empty:
        return {"总检验数": 0, "合格率": 0.0, "报废率": 0.0, "预警次数": 0}
    total_inspect = int(month_df["检测数量"].sum())
    total_pass = int(month_df["合格数量"].sum())
    total_scrap = int(month_df["报废数量"].sum())
    pass_rate = round(total_pass / total_inspect * 100, 2) if total_inspect > 0 else 0.0
    scrap_rate = round(total_scrap / total_inspect * 100, 2) if total_inspect > 0 else 0.0
    alerts = get_alerts_from_data(month_df)
    return {
        "总检验数": total_inspect,
        "合格率": pass_rate,
        "报废率": scrap_rate,
        "预警次数": len(alerts),
        "总合格数": total_pass,
        "总报废数": total_scrap,
    }


def get_monthly_pass_rate_trend(df, months_count=3):
    """
    最近N个月合格率趋势
    返回 DataFrame: [月份, 合格率, 报废率]
    """
    if df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["日期_dt"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期_dt"])
    df["月份"] = df["日期_dt"].dt.strftime("%Y-%m")
    monthly = df.groupby("月份").agg(
        检测数量=("检测数量", "sum"),
        合格数量=("合格数量", "sum"),
        报废数量=("报废数量", "sum"),
    ).reset_index()
    monthly["合格率"] = monthly.apply(
        lambda r: round(r["合格数量"] / r["检测数量"] * 100, 2) if r["检测数量"] > 0 else 0.0, axis=1)
    monthly["报废率"] = monthly.apply(
        lambda r: round(r["报废数量"] / r["检测数量"] * 100, 2) if r["检测数量"] > 0 else 0.0, axis=1)
    monthly = monthly.sort_values("月份").tail(months_count).reset_index(drop=True)
    return monthly[["月份", "合格率", "报废率"]]
