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
DEFECT_COLUMNS = ["锻造缺陷", "磕伤卡伤", "接刀痕", "过车欠车", "油沟", "倒角"]

ALL_COLUMNS = BASE_COLUMNS + DEFECT_COLUMNS

# 界面显示名 <-> 数据库列名 的映射
_DISPLAY_TO_DB = {"磕伤/卡伤": "磕伤卡伤", "过车/欠车": "过车欠车"}
_DB_TO_DISPLAY = {"磕伤卡伤": "磕伤/卡伤", "过车欠车": "过车/欠车"}


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


def export_report_to_excel(df, start_date, end_date):
    """导出筛选后的数据为Excel字节流"""
    import io
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="质量报告")
    output.seek(0)
    return output.getvalue()


# 兼容旧导入名 (app.py 或其他模块可能引用)
DEFECT_TYPE_FIELDS = DEFECT_COLUMNS
