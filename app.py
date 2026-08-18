"""
app.py - 车间质量检测跟踪系统（主程序）v3.3
舍弗勒银川工厂
功能：数据录入（含Excel上传）、实时监控、分级预警、分析报告、月报
存储：Turso云数据库持久化
变更：
  v3.2 → v3.3: 新增分析报告KPI+4图表布局, 新增月报模块(4Tab)
  v3.3.1: 图3改为柱+折线组合(报废数量+报废率), 图4改为月份聚合连续折线+独立时间范围
  保留全部原有业务逻辑, 仅增量新增展示层
"""
import streamlit as st
import pandas as pd
from datetime import datetime
from export_to_template import fill_template
from data_utils import delete_record
from config import SCRAP_COUNT_WARNING, SCRAP_COUNT_CRITICAL
from data_utils import get_alerts_from_data

from config import (
    PROCESSES,
    SHIFTS,
    WARNING_THRESHOLD,
    CONSECUTIVE_DAYS,
    SYSTEM_NAME,
    FACTORY_NAME,
    VERSION,
    CHART_COLORS,
)
from data_utils import (
    init_db,
    save_record,
    save_records_batch,
    load_all_records,
    calculate_scrap_rate,
    calculate_rework_rate,
    calculate_total_defect_rate,
    evaluate_warning,
    get_alerts_from_data,
    export_report_to_excel,
    DEFECT_TYPE_FIELDS,
    aggregate_model_pass_rate,
    aggregate_defect_summary,
    aggregate_top5_alerts,
    aggregate_monthly_trend,
    get_monthly_overview,
    get_monthly_pass_rate_trend,
)
from spc_chart import (
    render_trend_chart,
    render_pareto_chart,
    render_model_pass_rate_chart,
    render_model_fail_rate_chart,
    render_defect_analysis_chart,
    render_top5_alert_chart,
    render_monthly_trend_chart,
    render_monthly_pass_rate_trend_chart,
)

_PLOTLY_CONFIG = {"displayModeBar": False}

init_db()

st.set_page_config(
    page_title="{} - {}".format(FACTORY_NAME, SYSTEM_NAME),
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .alert-banner {
        background: linear-gradient(90deg, #dc3545 0%, #c82333 100%);
        color: white;
        padding: 14px 24px;
        border-radius: 10px;
        font-size: 16px;
        font-weight: bold;
        margin-bottom: 16px;
        animation: pulse 2s infinite;
        box-shadow: 0 4px 12px rgba(220,53,69,0.3);
    }
    .alert-item {
        background-color: #fff3cd;
        border-left: 4px solid #ffc107;
        padding: 10px 16px;
        border-radius: 6px;
        font-size: 14px;
        margin-bottom: 8px;
    }
    .kpi-value {
        font-size: 28px;
        font-weight: bold;
        text-align: center;
    }
    .kpi-label {
        font-size: 13px;
        color: #888;
        text-align: center;
        margin-bottom: 4px;
    }
    .chart-insight {
        font-size: 13px;
        color: #666;
        margin-top: 4px;
        margin-bottom: 16px;
    }
    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.85; }
    }
</style>
""", unsafe_allow_html=True)


def inject_auto_refresh(interval_sec: int = 120):
    st.markdown(
        '<meta http-equiv="refresh" content="{}">'.format(interval_sec),
        unsafe_allow_html=True,
    )


def _render_kpi_row(items):
    cols = st.columns(len(items))
    for col, (label, value, color) in zip(cols, items):
        col.markdown(
            '<div class="kpi-label">{}</div>'
            '<div class="kpi-value" style="color:{};">{}</div>'.format(label, color, value),
            unsafe_allow_html=True,
        )


def _render_insight(text):
    st.markdown('<div class="chart-insight">{}</div>'.format(text), unsafe_allow_html=True)


def _fmt_pct(val, digits=2):
    """格式化百分比展示: 0或空显示'-', 否则加% (只改展示不改存储)"""
    try:
        num = float(val)
        if num == 0:
            return "-"
        return "{:.{}f}%".format(num, digits)
    except (ValueError, TypeError):
        return "-"


# 6个标准缺陷字段 (带斜杠, 与load_all_records返回一致)
_DEFECT_DISPLAY_FIELDS = ["锻造缺陷", "磕伤/卡伤", "接刀痕", "过车/欠车", "油沟", "倒角"]

# 关键词匹配规则 (顺序: 过车>磕伤>接刀痕>倒角>油沟>锻造, 多匹配归第一个)
_DEFECT_KEYWORD_RULES = [
    ("过车/欠车", ["过车", "欠车"]),
    ("磕伤/卡伤", ["磕伤", "卡伤"]),
    ("接刀痕", ["接刀痕", "接刀"]),
    ("倒角", ["倒角"]),
    ("油沟", ["油沟"]),
    ("锻造缺陷", ["锻造", "锻件", "锻打"]),
]


def _backfill_defects_for_display(df):
    """展示层回填: 从报废原因文本提取缺陷数据, 填入6个结构化字段+漏黑列
    - 只改展示副本, 不改数据库
    - 漏黑独立成列, 不归到现有6个字段
    - 混合记录(漏黑+其他缺陷): 漏黑列和其他字段各填数量(有重复, 但保证数据不丢失)
    """
    df_out = df.copy()
    # 确保6个显示字段存在
    for f in _DEFECT_DISPLAY_FIELDS:
        if f not in df_out.columns:
            df_out[f] = ""
    # 新增漏黑列
    if "漏黑" not in df_out.columns:
        df_out["漏黑"] = ""

    for idx, row in df_out.iterrows():
        # 6字段全空才回填 (已有结构化数据的不动)
        all_empty = all(str(row.get(f, "")).strip() == "" for f in _DEFECT_DISPLAY_FIELDS)
        if not all_empty:
            continue
        # 报废数量>0才有意义
        try:
            scrap_num = int(row.get("报废数量", 0))
        except (ValueError, TypeError):
            scrap_num = 0
        if scrap_num <= 0:
            continue
        reason = str(row.get("报废原因", "")).strip()
        if not reason:
            continue

        # 漏黑独立判断
        has_leakblack = "漏黑" in reason

        # 6个标准缺陷关键词匹配 (归第一个)
        matched_field = None
        for field, keywords in _DEFECT_KEYWORD_RULES:
            for kw in keywords:
                if kw in reason:
                    matched_field = field
                    break
            if matched_field:
                break

        # 回填
        if has_leakblack:
            # 漏黑列填数量
            df_out.at[idx, "漏黑"] = str(scrap_num)
        if matched_field:
            # 匹配到的标准缺陷字段填数量
            df_out.at[idx, matched_field] = str(scrap_num)
        # 如果既没漏黑也没匹配标准缺陷, 不填 (椭圆超差等无法分类的)

    return df_out


def render_alert_banner(df=None):
    if df is None:
        df = load_all_records()
    alerts = get_alerts_from_data(df)

    if not alerts:
        st.success("✅ 当前所有型号质量状态正常，无活跃预警。")
        return

    st.markdown(
        '<div class="alert-banner">🚨 质量预警：当前有 {} 个型号/工序触发预警！</div>'.format(
            len(alerts)
        ),
        unsafe_allow_html=True,
    )
    for alert in alerts:
        if alert["等级"] == "批次不良":
            color, icon = "#dc3545", "🔴"
        else:
            color, icon = "#f0ad4e", "🟡"

        # ★ 新增: 查找该型号+工序最近一次有报废的数据日期
        sub = df[(df["型号"] == alert["型号"]) & (df["工序"] == alert["工序"])]
        scrap_sub = sub[sub["报废数量"] > 0] if not sub.empty else sub
        if not scrap_sub.empty:
            data_date = str(scrap_sub["日期"].max())
        elif not sub.empty:
            data_date = str(sub["日期"].max())
        else:
            data_date = "未知"

        st.markdown(
            '<div class="alert-item">'
            '{} <b>{}</b> | {} | 数据日期 <b>{}</b> | 当日报废 <b style="color:{};">{} 件</b> | {}'
            '</div>'.format(
                icon, alert["型号"], alert["工序"], data_date,
                color, alert["当日报废件数"], alert["原因"],
            ),
            unsafe_allow_html=True,
        )


def render_data_entry():
    st.header("📝 数据录入")
    st.caption("支持手动录入和Excel批量导入 | 数据保存至Turso云数据库 | 本页面不会自动刷新")

    with st.expander("📁 上传Excel文件（批量导入）", expanded=False):
        uploaded_file = st.file_uploader(
            "选择Excel文件（.xlsx / .xls）",
            type=["xlsx", "xls"],
            key="excel_uploader",
        )
        if uploaded_file is not None:
            try:
                df_upload = pd.read_excel(uploaded_file)
                df_upload = df_upload.fillna("")

                st.write("**预览（前5行）：**")
                st.dataframe(df_upload.head(), use_container_width=True)

                if st.button("📥 确认导入", type="primary", key="btn_import"):
                    col_map = {
                        "日期": "日期", "date": "日期",
                        "班次": "班次", "shift": "班次",
                        "型号": "型号", "model": "型号",
                        "产品类型": "产品类型",
                        "订单号": "订单号",
                        "车削操作员": "车削操作者",
                        "操作员": "车削操作者",
                        "车削操作者": "车削操作者",
                        "工序": "工序", "process": "工序",
                        "检测数量": "检测数量",
                        "合格数量": "合格数量",
                        "返修数量": "返修数量",
                        "报废数量": "报废数量",
                        "报废原因": "报废原因",
                        "返修原因": "返修原因",
                        "磕伤/卡伤": "磕伤卡伤",
                        "过车/欠车": "过车欠车",
                    }

                    df_upload = df_upload.rename(columns=col_map)

                    required_cols = ["日期", "班次", "型号", "工序", "检测数量", "合格数量", "返修数量", "报废数量"]
                    missing = [c for c in required_cols if c not in df_upload.columns]

                    if missing:
                        st.error("❌ Excel缺少必要列: {}".format(", ".join(missing)))
                    else:
                        optional_cols = ["产品类型", "订单号", "车削操作者", "报废原因", "返修原因"] + DEFECT_TYPE_FIELDS
                        for col in optional_cols:
                            if col not in df_upload.columns:
                                df_upload[col] = ""

                        for qty_col in ["检测数量", "合格数量", "返修数量", "报废数量"]:
                            df_upload[qty_col] = pd.to_numeric(df_upload[qty_col], errors="coerce").fillna(0).astype(int)

                        df_upload["报废率"] = df_upload.apply(
                            lambda r: calculate_scrap_rate(int(r["报废数量"]), int(r["检测数量"])), axis=1)
                        df_upload["返修率"] = df_upload.apply(
                            lambda r: calculate_rework_rate(int(r["返修数量"]), int(r["检测数量"])), axis=1)
                        df_upload["总不合格率"] = df_upload.apply(
                            lambda r: calculate_total_defect_rate(r["报废率"], r["返修率"]), axis=1)

                        if "日期" in df_upload.columns:
                            df_upload["日期"] = pd.to_datetime(df_upload["日期"], errors="coerce").dt.strftime("%Y-%m-%d")

                        records = df_upload.to_dict("records")
                        count = save_records_batch(records)
                        st.success("✅ 成功导入 {} 条记录！".format(count))

            except Exception as e:
                st.error("❌ 读取Excel失败: {}".format(str(e)))

    st.markdown("---")

    st.subheader("✍️ 手动录入")

    c1, c2, c3 = st.columns(3)
    with c1:
        entry_date = st.date_input("📅 日期", value=datetime.now(), key="entry_date")
    with c2:
        shift = st.selectbox("🕐 班次", SHIFTS, key="entry_shift")
    with c3:
        process = st.selectbox("⚙️ 工序", PROCESSES, key="entry_process")

    c4, c5, c6 = st.columns(3)
    with c4:
        model = st.text_input("🔧 型号", placeholder="例如: 6205-2RS", key="entry_model")
    with c5:
        product_type = st.text_input("📦 产品类型", placeholder="例如: 深沟球轴承", key="entry_ptype")
    with c6:
        order_no = st.text_input("📋 订单号", placeholder="例如: ORD-2026-0810", key="entry_order")

    c7, c8 = st.columns(2)
    with c7:
        operator = st.text_input("👷 车削操作者", placeholder="操作者姓名", key="entry_operator")
    with c8:
        st.write("")

    st.markdown("---")

    st.subheader("检测数据")
    c9, c10, c11, c12 = st.columns(4)
    with c9:
        inspect_qty = st.number_input("🔍 检测数量", min_value=0, max_value=999999, value=0, step=1, key="entry_inspect")
    with c10:
        pass_qty = st.number_input("✅ 合格数量", min_value=0, max_value=999999, value=0, step=1, key="entry_pass")
    with c11:
        rework_qty = st.number_input("🔧 返修数量", min_value=0, max_value=999999, value=0, step=1, key="entry_rework")
    with c12:
        scrap_qty = st.number_input("❌ 报废数量", min_value=0, max_value=999999, value=0, step=1, key="entry_scrap")

    scrap_rate = calculate_scrap_rate(scrap_qty, inspect_qty)
    rework_rate = calculate_rework_rate(rework_qty, inspect_qty)
    total_defect_rate = calculate_total_defect_rate(scrap_rate, rework_rate)

    st.info(
        "📊 自动计算 → **报废率: {:.2f}%** | **返修率: {:.2f}%** | **总不合格率: {:.2f}%**".format(
            scrap_rate, rework_rate, total_defect_rate))

    all_pass = (inspect_qty > 0 and inspect_qty == pass_qty)

    if all_pass:
        st.success("✅ 检测数量 = 合格数量，判定为全合格，缺陷类型已自动留空")

    st.markdown("---")

    st.subheader("⚠️ 缺陷类型（手动填写）")
    st.caption("请依次填写具体缺陷描述（如\"锻造折叠\"、\"磕伤卡伤\"等），无则留空")

    DEFECT_LABELS = {name: name for name in DEFECT_TYPE_FIELDS}

    defect_inputs = {}
    dcols = st.columns(3)
    for i in range(len(DEFECT_TYPE_FIELDS)):
        field_name = DEFECT_TYPE_FIELDS[i]
        label = DEFECT_LABELS[field_name]
        col_idx = i % 3
        with dcols[col_idx]:
            defect_inputs[field_name] = st.text_input(
                label,
                placeholder="填写{}描述...".format(label) if not all_pass else "",
                key="entry_defect_{}".format(i + 1),
                disabled=all_pass,
            )

    st.markdown("---")

    c15, c16 = st.columns(2)
    with c15:
        scrap_reason = st.text_input("❌ 报废原因（手动填写）", placeholder="请描述报废原因...", key="entry_scrap_reason")
    with c16:
        rework_reason = st.text_input("🔧 返修原因（手动填写）", placeholder="请输入返修原因...", key="entry_rework_reason")

    st.markdown("---")

    submitted = st.button("✅ 提交录入", type="primary", use_container_width=True)

    if submitted:
        if not model.strip():
            st.error("❌ 请输入型号！")
            return
        if not order_no.strip():
            st.error("❌ 请输入订单号！")
            return

        if pass_qty + rework_qty + scrap_qty != inspect_qty:
            st.warning(
                "⚠️ 合格({})+返修({})+报废({})={} ≠ 检测数量({})，请确认".format(
                    pass_qty, rework_qty, scrap_qty,
                    pass_qty + rework_qty + scrap_qty, inspect_qty,
                )
            )
        new_record = {
            "日期": entry_date.strftime("%Y-%m-%d"),
            "班次": shift,
            "型号": model.strip(),
            "产品类型": product_type.strip(),
            "订单号": order_no.strip(),
            "车削操作者": operator.strip(),
            "工序": process,
            "检测数量": inspect_qty,
            "合格数量": pass_qty,
            "返修数量": rework_qty,
            "报废数量": scrap_qty,
            "报废率": scrap_rate,
            "返修率": rework_rate,
            "总不合格率": total_defect_rate,
            "报废原因": scrap_reason.strip(),
            "返修原因": rework_reason.strip(),
        }

        for field in DEFECT_TYPE_FIELDS:
            new_record[field] = "" if all_pass else defect_inputs[field].strip()

        success = save_record(new_record)
        if success:
            st.success(
                "✅ 录入成功！型号: {} | 工序: {} | 日期: {} | "
                "检测{}件 | 报废率{:.2f}% | 返修率{:.2f}% | 总不合格率{:.2f}%".format(
                    model, process, entry_date.strftime("%Y-%m-%d"),
                    inspect_qty, scrap_rate, rework_rate, total_defect_rate,
                )
            )
            if scrap_qty >= SCRAP_COUNT_CRITICAL:
                st.error(
                    "🚨 **批次不良预警！** {} - {}: 当日报废 {} 件（≥{}件），"
                    "判定为小批次报废，需立即停线排查！".format(
                        model, process, scrap_qty, SCRAP_COUNT_CRITICAL
                    )
                )
            elif scrap_qty >= SCRAP_COUNT_WARNING:
                st.warning(
                    "⚠️ **重点关注预警！** {} - {}: 当日报废 {} 件（≥{}件），"
                    "请加强巡检频次并排查原因。".format(
                        model, process, scrap_qty, SCRAP_COUNT_WARNING
                    )
                )
        else:
            st.error("❌ 保存失败，请检查数据库连接。")

    st.markdown("---")

    st.subheader("📂 已录入数据")
    if st.button("🔄 加载/刷新数据", use_container_width=True, key="btn_load"):
        if "pending_delete_id" in st.session_state:
            del st.session_state["pending_delete_id"]
        st.rerun()

    df_current = load_all_records()
    if df_current.empty:
        st.info("暂无数据。")
    else:
        display_df = df_current.head(50).copy().reset_index(drop=True)
        display_df.insert(0, "序号", range(1, len(display_df) + 1))

        # ★ 百分比列格式化为带%字符串 (只改展示, 不影响导出)
        for _col in ["报废率", "返修率", "总不合格率"]:
            if _col in display_df.columns:
                display_df[_col] = display_df[_col].apply(lambda x: _fmt_pct(x))

        st.caption("共 {} 条记录（显示最近50条）".format(len(df_current)))
        show_cols = [c for c in display_df.columns if c != "id"]
        st.dataframe(display_df[show_cols], use_container_width=True, hide_index=True)

        st.markdown("---")
        st.subheader("🗑️ 删除记录")
        st.caption("选择一条记录后点击下方删除按钮，需二次确认防止误操作")

        display_options = []
        id_map = {}
        for _, row in display_df.iterrows():
            label = "#{} | {} | {} | {} | 报废{}件".format(
                row["序号"], row["日期"], row["型号"], row["工序"], row["报废数量"]
            )
            display_options.append(label)
            id_map[label] = row["id"]

        selected = st.selectbox("选择要删除的记录", display_options, key="delete_select")

        if selected:
            selected_id = id_map[selected]

            if "pending_delete_id" not in st.session_state:
                st.session_state["pending_delete_id"] = None

            if st.session_state["pending_delete_id"] == selected_id:
                st.warning("⚠️ **确认删除此记录？此操作不可撤销！**\n\n`{}`".format(selected))
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button("✅ 确认删除", type="primary", key="btn_confirm_del"):
                        ok = delete_record(selected_id)
                        if ok:
                            st.success("✅ 记录已删除！")
                            del st.session_state["pending_delete_id"]
                            st.rerun()
                        else:
                            st.error("❌ 删除失败，请重试。")
                with cc2:
                    if st.button("❌ 取消", key="btn_cancel_del"):
                        del st.session_state["pending_delete_id"]
                        st.rerun()
            else:
                if st.button("🗑️ 删除选中记录", type="secondary", key="btn_trigger_del"):
                    st.session_state["pending_delete_id"] = selected_id
                    st.rerun()


def render_dashboard():
     inject_auto_refresh(120)

     st.header("📊 实时监控看板")
     df = load_all_records()
     if not df.empty:
         latest_date = str(df["日期"].max())
         st.caption("⏱️ 本页面每2分钟自动刷新 | 数据最新日期：{}".format(latest_date))
     else:
         st.caption("⏱️ 本页面每2分钟自动刷新")
     render_alert_banner(df)
     st.markdown("---")

     if df.empty:
        st.info("暂无数据，请先在【数据录入】中录入检测记录。")
        return

     st.subheader("📈 报废率趋势")
     available_models = sorted(df["型号"].dropna().unique().tolist())
     available_processes = sorted(df["工序"].dropna().unique().tolist())

     if not available_models:
        st.info("暂无型号数据。")
        return

     tcol1, tcol2 = st.columns(2)
     with tcol1:
        trend_model = st.selectbox("选择型号", available_models, key="dash_model")
     with tcol2:
        trend_process = st.selectbox(
            "选择工序（可选）", ["全部"] + available_processes, key="dash_proc"
        )

     proc_filter = None if trend_process == "全部" else trend_process
     fig = render_trend_chart(df, trend_model, proc_filter)
     st.plotly_chart(fig, use_container_width=True)


def render_warning_center():
    st.header("🚨 分级预警中心")

    with st.expander("📋 预警规则说明", expanded=False):
        st.markdown("""

        | 等级 | 触发条件 | 颜色 | 处置要求 |
        |------|----------|------|----------|
        | 重点关注 | 当日报废 **≥ {}** 件 | 🟡 黄色 | 加强巡检频次，排查原因 |
        | 批次不良 | 当日报废 **≥ {}** 件 | 🔴 红色 | 判定为小批次报废，立即停线排查 |
        """.format(SCRAP_COUNT_WARNING, SCRAP_COUNT_CRITICAL))

    df = load_all_records()
    alerts = get_alerts_from_data(df)

    if not alerts:
        st.success("✅ 当前无活跃预警。")
        return

    st.subheader("⚠️ 活跃预警（{} 项）".format(len(alerts)))
    for idx, alert in enumerate(alerts):
        with st.container():
            ac1, ac2, ac3 = st.columns([2, 1, 4])
            with ac1:
                st.markdown("**{}**".format(alert["型号"]))
                st.caption("工序: {}".format(alert["工序"]))
            with ac2:
                if alert["等级"] == "批次不良":
                    color = "#dc3545"
                else:
                    color = "#f0ad4e"
                label = "{} 件".format(alert["当日报废件数"])
                st.markdown(
                    '<span style="color:{};font-size:22px;font-weight:bold;">'
                    '{}</span>'.format(color, label),
                    unsafe_allow_html=True,
                )
            with ac3:
                if alert["等级"] == "批次不良":
                    st.error("📌 {}".format(alert["原因"]))
                else:
                    st.warning("📌 {}".format(alert["原因"]))
            st.markdown("---")


def render_analysis_report():
    inject_auto_refresh(120)

    st.header("🔍 分析报告")
    st.caption("⏱️ 本页面每2分钟自动刷新")

    df = load_all_records()
    if df.empty:
        st.info("暂无数据。")
        return

    # ---------- 时间筛选 + 型号筛选 ----------
    fcol1, fcol2, fcol3 = st.columns(3)
    with fcol1:
        start_date = st.date_input("开始日期", value=datetime(2026, 1, 1), key="rpt_start")
    with fcol2:
        end_date = st.date_input("结束日期", value=datetime.now(), key="rpt_end")
    with fcol3:
        all_models = ["全部"] + sorted(df["型号"].dropna().unique().tolist())
        report_model = st.selectbox("型号", all_models, key="rpt_model")

    df_rpt = df.copy()
    df_rpt["日期_dt"] = pd.to_datetime(df_rpt["日期"], errors="coerce")
    mask = (
        (df_rpt["日期_dt"] >= pd.Timestamp(start_date))
        & (df_rpt["日期_dt"] <= pd.Timestamp(end_date))
    )
    df_rpt = df_rpt[mask]
    if report_model != "全部":
        df_rpt = df_rpt[df_rpt["型号"] == report_model]

    if df_rpt.empty:
        st.warning("所选条件下无数据。")
        return

    sel_model = None if report_model == "全部" else report_model

    # ============================================================
    # KPI卡片行 (4个)
    # ============================================================
    total_inspect = int(df_rpt["检测数量"].sum())
    total_pass = int(df_rpt["合格数量"].sum())
    total_scrap = int(df_rpt["报废数量"].sum())
    pass_rate = round(total_pass / total_inspect * 100, 1) if total_inspect > 0 else 0.0
    scrap_rate = round(total_scrap / total_inspect * 100, 1) if total_inspect > 0 else 0.0
    alert_count = len(get_alerts_from_data(df_rpt))

    pass_color = CHART_COLORS["success"] if pass_rate >= 95 else (CHART_COLORS["warning"] if pass_rate >= 90 else CHART_COLORS["danger"])
    scrap_color = CHART_COLORS["success"] if scrap_rate < 2 else (CHART_COLORS["warning"] if scrap_rate < 5 else CHART_COLORS["danger"])
    alert_color = CHART_COLORS["success"] if alert_count == 0 else (CHART_COLORS["warning"] if alert_count < 3 else CHART_COLORS["danger"])

    st.markdown("### 📊 质量概览")
    _render_kpi_row([
        ("总检测数", "{}件".format(total_inspect), CHART_COLORS["primary"]),
        ("合格率", "{:.1f}%".format(pass_rate), pass_color),
        ("报废率", "{:.1f}%".format(scrap_rate), scrap_color),
        ("预警数", "{}个".format(alert_count), alert_color),
    ])
    st.markdown("---")

    # ============================================================
    # 第二行: 图1(型号不合格率问题看板) | 图2(缺陷不良分析)
    # ============================================================
    st.markdown("### 📈 型号不合格率 & 缺陷不良分析")
    col_left, col_right = st.columns(2)

    with col_left:
        fig1, insight1 = render_model_fail_rate_chart(df_rpt, CHART_COLORS)
        st.plotly_chart(fig1, use_container_width=True, config=_PLOTLY_CONFIG)
        _render_insight(insight1)

    with col_right:
        fig2, top3_df, insight2 = render_defect_analysis_chart(df_rpt, CHART_COLORS)
        st.plotly_chart(fig2, use_container_width=True, config=_PLOTLY_CONFIG)
        _render_insight(insight2)
        if not top3_df.empty:
            st.markdown("**Top3缺陷明细**")
            top3_display = top3_df.copy()
            if "占比" in top3_display.columns:
                top3_display["占比"] = top3_display["占比"].apply(lambda x: _fmt_pct(x, 1))
            st.dataframe(top3_display, use_container_width=True, hide_index=True)

    st.markdown("---")

    # ============================================================
    # ★★★ 第三行: 图3 近N天不良Top5预警看板 (v3.3.1 改为柱+折线组合)
    # ============================================================
    st.markdown("### 🚨 近N天不良Top5预警看板")
    st.caption("图1的下钻视角：快速筛选过去X天里报废达到Y件的型号及其完整表现")

    tc1, tc2 = st.columns(2)
    with tc1:
        # ★ 时间范围: 近2/3/5/7/30天
        top_days = st.selectbox(
            "时间范围",
            [2, 3, 5, 7, 30],
            format_func=lambda x: "近{}天".format(x),
            index=3,  # 默认选近7天
            key="top5_days"
        )
    with tc2:
        top_threshold = st.selectbox("报废件数阈值", [5, 10, 15, 20], key="top5_threshold")

    # ★ 图3用完整df (不依赖顶部日期筛选, 按自己的时间范围筛选)
    fig3, detail_df, insight3 = render_top5_alert_chart(df, top_days, top_threshold, CHART_COLORS)
    st.plotly_chart(fig3, use_container_width=True, config=_PLOTLY_CONFIG)

    if not detail_df.empty:
        st.markdown("**Top5问题型号清单**")
        detail_display = detail_df.copy()
        if "报废率" in detail_display.columns:
            detail_display["报废率"] = detail_display["报废率"].apply(lambda x: _fmt_pct(x, 1))
        st.dataframe(detail_display, use_container_width=True, hide_index=True)
    _render_insight(insight3)

    st.markdown("---")

    # ============================================================
    # ★★★ 第四行: 图4 型号缺陷模式月度趋势图 (v3.3.1 改为月份聚合+独立时间范围)
    # ============================================================
    st.markdown("### 📉 型号缺陷模式月度趋势")
    st.caption("按月份聚合，连续折线展示缺陷变化趋势，判断改善措施是否有效")

    mc1, mc2, mc3 = st.columns(3)
    with mc1:
        # ★ 独立时间范围下拉框: 近3/6/12个月, 默认近6个月
        trend_months = st.selectbox(
            "时间范围",
            [3, 6, 12],
            format_func=lambda x: "近{}个月".format(x),
            index=1,  # 默认选近6个月
            key="mt_months"
        )
    with mc2:
        trend_models = ["全部"] + sorted(df["型号"].dropna().unique().tolist())
        trend_model_sel = st.selectbox("型号选择", trend_models, key="mt_model")
    with mc3:
        trend_defects = ["全部", "锻造缺陷", "磕伤/卡伤", "接刀痕", "过车/欠车", "油沟", "倒角", "漏黑",
                         "椭圆超差", "内径垫伤", "滚道对称点过大", "对称度超差", "幅高椭圆"]
        trend_defect_sel = st.selectbox("缺陷类型选择", trend_defects, key="mt_defect")

    # ★ 图4用完整df + 独立months参数 (不依赖顶部日期筛选)
    fig4, insight4 = render_monthly_trend_chart(df, trend_model_sel, trend_defect_sel, trend_months, CHART_COLORS)
    if trend_defect_sel == "全部":
        st.caption("💡 点击图例可显示/隐藏缺陷类型（默认显示前3种最严重的，其余点击图例即可展开）")
    st.plotly_chart(fig4, use_container_width=True, config=_PLOTLY_CONFIG)
    _render_insight(insight4)

    st.markdown("---")

    # ============================================================
    # 保留原有: 柏拉图
    # ============================================================
    st.subheader("📊 缺陷类型柏拉图")
    fig_pareto = render_pareto_chart(df_rpt, sel_model)
    st.plotly_chart(fig_pareto, use_container_width=True)

    st.markdown("---")

    # ============================================================
    # 保留原有: 总报告表格 (v3.3.2: 百分比格式化 + 列名对齐 + 展示层回填)
    # ============================================================
    st.subheader("📋 总报告明细")

    # ★ 任务B4: 展示层回填 - 从报废原因/返修原因文本提取缺陷数据
    df_display = _backfill_defects_for_display(df_rpt)

    # ★ 任务B3: 列名与数据录入页对齐 (使用带斜杠的显示名 + 漏黑列 + 5个新缺陷字段)
    display_cols = [
        "日期", "班次", "型号", "产品类型", "订单号", "车削操作者",
        "工序", "检测数量", "合格数量", "返修数量", "报废数量",
        "报废率", "返修率", "总不合格率",
        "锻造缺陷", "磕伤/卡伤", "接刀痕", "过车/欠车", "油沟", "倒角", "漏黑",
        "椭圆超差", "内径垫伤", "滚道对称点过大", "对称度超差", "幅高椭圆",
        "报废原因", "返修原因",
    ]

    existing = [c for c in display_cols if c in df_display.columns]

    # ★ 任务B2: 百分比列格式化为带%字符串 (只改展示, 不影响导出)
    report_display = df_display[existing].copy()
    for _col in ["报废率", "返修率", "总不合格率"]:
        if _col in report_display.columns:
            report_display[_col] = report_display[_col].apply(lambda x: _fmt_pct(x))

    st.dataframe(report_display, use_container_width=True, hide_index=True)

    st.subheader("📥 导出报告")
    if st.button("生成Excel报告", type="primary", use_container_width=True, key="btn_export"):
        excel_bytes = export_report_to_excel(df_rpt, str(start_date), str(end_date))
        st.download_button(
            label="📥 下载Excel",
            data=excel_bytes,
            file_name="质量报告_{}_{}.xlsx".format(start_date, end_date),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        st.success("✅ 报告已生成！")

    st.markdown("---")
    st.subheader("📥 导出到模板")
    st.caption("上传Excel模板，系统自动识别表头并将全部检测记录填入，保留原模板格式、公式、合并单元格及列宽")

    template_file = st.file_uploader("选择Excel模板文件（.xlsx）", type=["xlsx"], key="template_uploader")

    if template_file is not None:
        if st.button("📤 填充模板并下载", type="primary", use_container_width=True, key="btn_fill_template"):
            with st.spinner("正在读取数据并填充模板..."):
                result_bytes, status_msg = fill_template(template_file.read())

            if result_bytes:
                st.success(status_msg)
                st.download_button(
                    label="📥 下载 质量检测报告.xlsx",
                    data=result_bytes,
                    file_name="质量检测报告.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key="btn_download_template",
                )
            else:
                st.error(status_msg)


def render_monthly_report():
    """月报模块: 4个Tab (当月概览/型号分析/缺陷分析/预警回顾)"""
    st.header("📅 月报")
    st.caption("按自然月汇总质量数据, 自动生成分析总结")

    df = load_all_records()
    if df.empty:
        st.info("暂无数据，请先录入检测记录。")
        return

    df["日期_dt"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期_dt"])
    df["月份"] = df["日期_dt"].dt.strftime("%Y-%m")
    available_months = sorted(df["月份"].unique().tolist(), reverse=True)
    if not available_months:
        st.info("无有效日期数据。")
        return

    mcol1, mcol2 = st.columns(2)
    with mcol1:
        selected_month = st.selectbox("选择月份", available_months, key="mr_month")
    with mcol2:
        st.caption("当前查看: {}年{}月".format(selected_month[:4], selected_month[5:7]))

    month_df = df[df["月份"] == selected_month].copy()
    if month_df.empty:
        st.warning("所选月份无数据。")
        return

    prev_month_idx = available_months.index(selected_month) + 1
    prev_month = available_months[prev_month_idx] if prev_month_idx < len(available_months) else None

    overview = get_monthly_overview(month_df, selected_month)
    prev_overview = get_monthly_overview(df[df["月份"] == prev_month], prev_month) if prev_month else None

    tab1, tab2, tab3, tab4 = st.tabs(["📋 当月概览", "🔧 型号分析", "⚠️ 缺陷分析", "🚨 预警回顾"])

    with tab1:
        st.subheader("当月KPI概览")
        pass_color = CHART_COLORS["success"] if overview["合格率"] >= 95 else (CHART_COLORS["warning"] if overview["合格率"] >= 90 else CHART_COLORS["danger"])
        scrap_color = CHART_COLORS["success"] if overview["报废率"] < 2 else (CHART_COLORS["warning"] if overview["报废率"] < 5 else CHART_COLORS["danger"])
        _render_kpi_row([
            ("总检验数", "{}件".format(overview["总检验数"]), CHART_COLORS["primary"]),
            ("合格率", "{:.1f}%".format(overview["合格率"]), pass_color),
            ("报废率", "{:.1f}%".format(overview["报废率"]), scrap_color),
            ("预警次数", "{}次".format(overview["预警次数"]), CHART_COLORS["danger"] if overview["预警次数"] > 0 else CHART_COLORS["success"]),
        ])

        st.markdown("---")
        st.subheader("月度合格率趋势图")
        fig_trend = render_monthly_pass_rate_trend_chart(df, CHART_COLORS)
        st.plotly_chart(fig_trend, use_container_width=True, config=_PLOTLY_CONFIG)

        if prev_overview and prev_overview["总检验数"] > 0:
            pass_diff = round(overview["合格率"] - prev_overview["合格率"], 1)
            arrow = "↑" if pass_diff > 0 else "↓"
            st.info("📊 合格率环比上月{}{}个百分点".format(arrow, abs(pass_diff)))
        else:
            st.info("📊 上月无数据，暂无法做环比分析")

    with tab2:
        st.subheader("型号合格率分析")
        model_rate = aggregate_model_pass_rate(month_df)
        if model_rate.empty:
            st.info("当月无型号数据。")
        else:
            fig_model, model_insight = render_model_pass_rate_chart(month_df, CHART_COLORS)
            st.plotly_chart(fig_model, use_container_width=True, config=_PLOTLY_CONFIG)
            _render_insight(model_insight)

            st.markdown("**型号不良明细表**")
            st.dataframe(model_rate, use_container_width=True, hide_index=True)

    with tab3:
        st.subheader("缺陷柏拉图分析")
        fig_defect, top3_defect, defect_insight = render_defect_analysis_chart(month_df, CHART_COLORS)
        st.plotly_chart(fig_defect, use_container_width=True, config=_PLOTLY_CONFIG)
        _render_insight(defect_insight)

        if not top3_defect.empty:
            st.markdown("**Top5缺陷明细表**")
            top5_defect = top3_defect.head(5)
            st.dataframe(top5_defect, use_container_width=True, hide_index=True)

    with tab4:
        st.subheader("当月预警回顾")
        month_alerts = get_alerts_from_data(month_df)
        active_alerts = get_alerts_from_data(df)

        red_count = sum(1 for a in month_alerts if a["等级"] == "批次不良")
        yellow_count = sum(1 for a in month_alerts if a["等级"] == "重点关注")

        _render_kpi_row([
            ("当月预警总数", "{}次".format(len(month_alerts)), CHART_COLORS["primary"]),
            ("批次不良(红)", "{}次".format(red_count), CHART_COLORS["danger"]),
            ("重点关注(黄)", "{}次".format(yellow_count), CHART_COLORS["warning"]),
            ("仍活跃", "{}次".format(len(active_alerts)), CHART_COLORS["danger"] if active_alerts else CHART_COLORS["success"]),
        ])

        st.markdown("---")
        if month_alerts:
            for alert in month_alerts:
                if alert["等级"] == "批次不良":
                    st.error("🔴 {} | {} | 报废{}件 | {}".format(alert["型号"], alert["工序"], alert["当日报废件数"], alert["原因"]))
                else:
                    st.warning("🟡 {} | {} | 报废{}件 | {}".format(alert["型号"], alert["工序"], alert["当日报废件数"], alert["原因"]))
        else:
            st.success("✅ 当月无预警触发")

    st.markdown("---")
    st.subheader("📝 月报总结")

    if prev_overview and prev_overview["总检验数"] > 0:
        pass_diff = round(overview["合格率"] - prev_overview["合格率"], 1)
        pass_arrow = "↑" if pass_diff > 0 else "↓"
        pass_change = "{}{}个百分点".format(pass_arrow, abs(pass_diff))
    else:
        pass_change = "上月无数据"

    defect_summary = aggregate_defect_summary(month_df)
    main_defect = defect_summary.iloc[0]["缺陷类型"] if not defect_summary.empty else "无"
    main_defect_pct = defect_summary.iloc[0]["占比"] if not defect_summary.empty else 0

    model_rate_df = aggregate_model_pass_rate(month_df)
    if not model_rate_df.empty:
        worst_model = model_rate_df.nsmallest(1, "合格率").iloc[0]["型号"]
    else:
        worst_model = "无"

    if len(month_alerts) > 0:
        process_rate = round((1 - len(active_alerts) / max(len(month_alerts), 1)) * 100, 1)
        process_rate = max(0, min(100, process_rate))
    else:
        process_rate = 100.0

    summary = "本月共检验**{}件**，合格率**{:.1f}%**，环比上月**{}**。不良数**{}件**，主要集中在**{}**型号的**{}**缺陷类型(占比{:.1f}%)。本月触发预警**{}次**，处理率**{:.1f}%**。建议下月重点关注：**{}**型号的**{}**缺陷类型。".format(
        overview["总检验数"],
        overview["合格率"],
        pass_change,
        overview["总报废数"],
        worst_model,
        main_defect,
        main_defect_pct,
        len(month_alerts),
        process_rate,
        worst_model,
        main_defect,
    )
    st.info(summary)


def render_sidebar():
    with st.sidebar:
        st.markdown("## 🏭 {}".format(FACTORY_NAME))
        st.markdown("### {}".format(SYSTEM_NAME))
        st.markdown("---")

        page = st.radio(
            "📌 功能导航",
            ["📝 数据录入", "📊 实时监控", "🚨 分级预警", "🔍 分析报告", "📅 月报"],
            key="nav_page",
        )

        st.markdown("---")

        df = load_all_records()
        alerts = get_alerts_from_data(df)

        if alerts:
            st.error("🚨 {} 个活跃预警".format(len(alerts)))
        else:
            st.success("✅ 系统正常")

        st.markdown("---")
        st.markdown("**数据概览**")
        st.caption("检测记录: {} 条".format(len(df)))
        if not df.empty:
            st.caption("覆盖型号: {} 个".format(df["型号"].nunique()))
            st.caption("覆盖工序: {} 个".format(df["工序"].nunique()))

        st.markdown("---")
        st.markdown("**预警阈值**")
        st.caption("报废率 > {}% 且连续{}天".format(WARNING_THRESHOLD, CONSECUTIVE_DAYS))

        st.markdown("---")
        st.caption("⏱️ 实时监控 / 分析报告：每2分钟自动刷新")
        st.caption("📝 数据录入：完全手动，不自动刷新")
        st.markdown("---")
        st.caption("{} | {}".format(SYSTEM_NAME, VERSION))
        st.caption("数据存储: Turso云数据库")

    return page


def main():
    page = render_sidebar()

    st.markdown(
        '<h1 style="color:#1a1a2e;">🏭 {}</h1>'.format(SYSTEM_NAME),
        unsafe_allow_html=True,
    )
    st.caption("{} | 车间质量检测一体化平台 | 数据持久化存储".format(FACTORY_NAME))

    if page == "📝 数据录入":
        render_data_entry()
    elif page == "📊 实时监控":
        render_dashboard()
    elif page == "🚨 分级预警":
        render_warning_center()
    elif page == "🔍 分析报告":
        render_analysis_report()
    elif page == "📅 月报":
        render_monthly_report()


if __name__ == "__main__":
    main()
