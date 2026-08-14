"""
app.py - 车间质量检测跟踪系统（主程序）v3.2
舍弗勒银川工厂
功能：数据录入（含Excel上传）、实时监控、分级预警、分析报告
存储：SQLite持久化
变更：简化录入界面，缺陷类型改为手动输入，新增总不合格率
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
)
from spc_chart import render_trend_chart, render_pareto_chart

# ==================== 初始化数据库 ====================
init_db()

# ==================== 页面配置 ====================
st.set_page_config(
    page_title="{} - {}".format(FACTORY_NAME, SYSTEM_NAME),
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==================== 自定义CSS ====================
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
    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.85; }
    }
</style>
""", unsafe_allow_html=True)


# ==================== 自动刷新注入（仅特定页面调用） ====================
def inject_auto_refresh(interval_sec: int = 30):
    st.markdown(
        '<meta http-equiv="refresh" content="{}">'.format(interval_sec),
        unsafe_allow_html=True,
    )


# ==================== 预警横幅 ====================
def render_alert_banner():
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

        st.markdown(
            '<div class="alert-item">'
            '{} <b>{}</b> | {} | 当日报废 <b style="color:{};">{} 件</b> | {}'
            '</div>'.format(
                icon, alert["型号"], alert["工序"],
                color, alert["当日报废件数"], alert["原因"],
            ),
            unsafe_allow_html=True,
        )


# ==================== 模块1: 数据录入（无自动刷新） ====================
def render_data_entry():
    st.header("📝 数据录入")
    st.caption("支持手动录入和Excel批量导入 | 数据保存至SQLite数据库 | 本页面不会自动刷新")

    # ---------- Excel上传区 ----------
    with st.expander("📁 上传Excel文件（批量导入）", expanded=False):
        uploaded_file = st.file_uploader(
            "选择Excel文件（.xlsx / .xls）",
            type=["xlsx", "xls"],
            key="excel_uploader",
        )
        if uploaded_file is not None:
            try:
                df_upload = pd.read_excel(uploaded_file)
                st.write("**预览（前5行）：**")
                st.dataframe(df_upload.head(), use_container_width=True)

                if st.button("📥 确认导入", type="primary", key="btn_import"):
                    col_map = {
                        "日期": "日期", "date": "日期",
                        "班次": "班次", "shift": "班次",
                        "型号": "型号", "model": "型号",
                        "产品类型": "产品类型",
                        "订单号": "订单号",
                        "车削操作员": "车削操作者", "操作员": "车削操作者",
                        "车削操作者": "车削操作者",
                        "工序": "工序", "process": "工序",
                        "检测数量": "检测数量",
                        "合格数量": "合格数量",
                        "返修数量": "返修数量",
                        "报废数量": "报废数量",
                        "报废原因": "报废原因",
                        "返修原因": "返修原因",
                    }
                    # 映射6个缺陷类型列
                    for i in range(1, 7):
                        col_map["缺陷类型{}".format(i)] = "缺陷类型{}".format(i)

                    df_upload = df_upload.rename(columns=col_map)

                    required_cols = [
                        "日期", "班次", "型号", "工序",
                        "检测数量", "合格数量", "返修数量", "报废数量",
                    ]
                    missing = [
                        c for c in required_cols if c not in df_upload.columns]
                    if missing:
                        st.error("❌ Excel缺少必要列: {}".format(", ".join(missing)))
                    else:
                        optional_cols = [
                            "产品类型", "订单号", "车削操作者",
                            "报废原因", "返修原因",
                        ] + DEFECT_TYPE_FIELDS
                        for col in optional_cols:
                            if col not in df_upload.columns:
                                df_upload[col] = ""

                        for qty_col in ["检测数量", "合格数量", "返修数量", "报废数量"]:
                            df_upload[qty_col] = pd.to_numeric(
                                df_upload[qty_col], errors="coerce"
                            ).fillna(0).astype(int)

                        df_upload["报废率"] = df_upload.apply(
                            lambda r: calculate_scrap_rate(
                                int(r["报废数量"]), int(r["检测数量"])
                            ), axis=1
                        )
                        df_upload["返修率"] = df_upload.apply(
                            lambda r: calculate_rework_rate(
                                int(r["返修数量"]), int(r["检测数量"])
                            ), axis=1
                        )
                        df_upload["总不合格率"] = df_upload.apply(
                            lambda r: calculate_total_defect_rate(
                                r["报废率"], r["返修率"]
                            ), axis=1
                        )

                        df_upload["日期"] = pd.to_datetime(
                            df_upload["日期"], errors="coerce"
                        ).dt.strftime("%Y-%m-%d")

                        records = df_upload.to_dict("records")
                        count = save_records_batch(records)
                        st.success("✅ 成功导入 {} 条记录！".format(count))
            except Exception as e:
                st.error("❌ 读取Excel失败: {}".format(str(e)))

    st.markdown("---")

    # ---------- 手动录入区 ----------
    st.subheader("✍️ 手动录入")

    c1, c2, c3 = st.columns(3)
    with c1:
        entry_date = st.date_input(
            "📅 日期", value=datetime.now(), key="entry_date")
    with c2:
        shift = st.selectbox("🕐 班次", SHIFTS, key="entry_shift")
    with c3:
        process = st.selectbox("⚙️ 工序", PROCESSES, key="entry_process")

    c4, c5, c6 = st.columns(3)
    with c4:
        model = st.text_input(
            "🔧 型号",
            placeholder="例如: 6205-2RS",
            key="entry_model")
    with c5:
        product_type = st.text_input(
            "📦 产品类型",
            placeholder="例如: 深沟球轴承",
            key="entry_ptype")
    with c6:
        order_no = st.text_input(
            "📋 订单号",
            placeholder="例如: ORD-2026-0810",
            key="entry_order")

    c7, c8 = st.columns(2)
    with c7:
        operator = st.text_input(
            "👷 车削操作者",
            placeholder="操作者姓名",
            key="entry_operator")
    with c8:
        st.write("")

    st.markdown("---")

    # 检测数据
    st.subheader("检测数据")
    c9, c10, c11, c12 = st.columns(4)
    with c9:
        inspect_qty = st.number_input(
            "🔍 检测数量",
            min_value=0,
            max_value=999999,
            value=0,
            step=1,
            key="entry_inspect")
    with c10:
        pass_qty = st.number_input(
            "✅ 合格数量",
            min_value=0,
            max_value=999999,
            value=0,
            step=1,
                key="entry_pass")
    with c11:
        rework_qty = st.number_input(
            "🔧 返修数量",
            min_value=0,
            max_value=999999,
            value=0,
            step=1,
            key="entry_rework")
    with c12:
        scrap_qty = st.number_input(
            "❌ 报废数量",
            min_value=0,
            max_value=999999,
            value=0,
            step=1,
            key="entry_scrap")

    scrap_rate = calculate_scrap_rate(scrap_qty, inspect_qty)
    rework_rate = calculate_rework_rate(rework_qty, inspect_qty)
    total_defect_rate = calculate_total_defect_rate(scrap_rate, rework_rate)

    st.info(
        "📊 自动计算 → **报废率: {:.2f}%** | **返修率: {:.2f}%** | **总不合格率: {:.2f}%**".format(
            scrap_rate,
            rework_rate,
            total_defect_rate))

    # ★ 全合格判定
    all_pass = (inspect_qty > 0 and inspect_qty == pass_qty)

    if all_pass:
        st.success(
            "✅ 检测数量 = 合格数量，判定为全合格，缺陷类型已自动留空"
        )

    st.markdown("---")

    # ★ 6个缺陷类型手动输入框（真实缺陷名称标签）
    st.subheader("⚠️ 缺陷类型（手动填写）")
    st.caption("请依次填写具体缺陷描述（如\"锻造折叠\"、\"磕伤卡伤\"等），无则留空")

    # DEFECT_TYPE_FIELDS 已更新为真实名称，无需额外映射
    DEFECT_LABELS = {name: name for name in DEFECT_TYPE_FIELDS}

    defect_inputs = {}
    dcols = st.columns(3)
    for i in range(6):
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

    # 报废/返修原因
    c15, c16 = st.columns(2)
    with c15:
        scrap_reason = st.text_input(
            "❌ 报废原因（手动填写）",
            placeholder="请描述报废原因...",
            key="entry_scrap_reason",
        )
    with c16:
        rework_reason = st.text_input(
            "🔧 返修原因（手动填写）",
            placeholder="请输入返修原因...",
            key="entry_rework_reason",
        )

    st.markdown("---")

    # 提交按钮
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

        # 写入6个缺陷类型字段（全合格时强制为空字符串）
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

    # ====== 已录入数据 + 删除按钮 ======
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

        st.caption("共 {} 条记录（显示最近50条）".format(len(df_current)))
        show_cols = [c for c in display_df.columns if c != "id"]
        st.dataframe(
            display_df[show_cols],
            use_container_width=True,
            hide_index=True)

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

        selected = st.selectbox(
            "选择要删除的记录",
            display_options,
            key="delete_select",
        )

        if selected:
            selected_id = id_map[selected]

            if "pending_delete_id" not in st.session_state:
                st.session_state["pending_delete_id"] = None

            if st.session_state["pending_delete_id"] == selected_id:
                st.warning("⚠️ **确认删除此记录？此操作不可撤销！**\n\n`{}`".format(selected))
                cc1, cc2 = st.columns(2)
                with cc1:
                    if st.button(
                        "✅ 确认删除",
                        type="primary",
                        key="btn_confirm_del"):
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
                if st.button(
                    "🗑️ 删除选中记录",
                    type="secondary",
                    key="btn_trigger_del"):
                    st.session_state["pending_delete_id"] = selected_id
                    st.rerun()


# ==================== 模块2: 实时监控看板（自动刷新） ====================
def render_dashboard():
    inject_auto_refresh(30)

    st.header("📊 实时监控看板")
    st.caption("⏱️ 本页面每30秒自动刷新")
    render_alert_banner()
    st.markdown("---")

    df = load_all_records()
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


# ==================== 模块3: 分级预警中心（无自动刷新） ====================
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


# ==================== 模块4: 分析报告（自动刷新） ====================
def render_analysis_report():
    inject_auto_refresh(30)

    st.header("🔍 分析报告")
    st.caption("⏱️ 本页面每30秒自动刷新")

    df = load_all_records()
    if df.empty:
        st.info("暂无数据。")
        return

    fcol1, fcol2, fcol3 = st.columns(3)
    with fcol1:
        start_date = st.date_input(
            "开始日期", value=datetime(
        2026, 1, 1), key="rpt_start")
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

    # 柏拉图
    st.subheader("📊 缺陷类型柏拉图")
    sel_model = None if report_model == "全部" else report_model
    fig_pareto = render_pareto_chart(df_rpt, sel_model)
    st.plotly_chart(fig_pareto, use_container_width=True)

    st.markdown("---")

    # 总报告表格
    st.subheader("📋 总报告明细")
    display_cols = [
        "日期", "班次", "型号", "产品类型", "订单号", "车削操作者",
        "工序", "检测数量", "合格数量", "返修数量", "报废数量",
        "报废率", "返修率", "总不合格率",
    ] + DEFECT_TYPE_FIELDS + ["报废原因", "返修原因"]

    existing = [c for c in display_cols if c in df_rpt.columns]
    st.dataframe(df_rpt[existing], use_container_width=True, hide_index=True)

    # ========== Excel导出 ==========
    st.subheader("📥 导出报告")
    if st.button(
        "生成Excel报告",
        type="primary",
        use_container_width=True,
        key="btn_export"):
        excel_bytes = export_report_to_excel(
            df_rpt, str(start_date), str(end_date))
        st.download_button(
            label="📥 下载Excel",
            data=excel_bytes,
            file_name="质量报告_{}_{}.xlsx".format(
        start_date,
        end_date),
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            )
        st.success("✅ 报告已生成！")

    # ========== 导出到模板 ==========
    st.markdown("---")
    st.subheader("📥 导出到模板")
    st.caption("上传Excel模板，系统自动识别表头并将全部检测记录填入，保留原模板格式、公式、合并单元格及列宽")

    template_file = st.file_uploader(
        "选择Excel模板文件（.xlsx）",
        type=["xlsx"],
        key="template_uploader",
    )

    if template_file is not None:
        if st.button(
            "📤 填充模板并下载",
            type="primary",
            use_container_width=True,
            key="btn_fill_template",
        ):
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


# ==================== 侧边栏 ====================
def render_sidebar():
    with st.sidebar:
        st.markdown("## 🏭 {}".format(FACTORY_NAME))
        st.markdown("### {}".format(SYSTEM_NAME))
        st.markdown("---")

        page = st.radio(
            "📌 功能导航",
            ["📝 数据录入", "📊 实时监控", "🚨 分级预警", "🔍 分析报告"],
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
        st.caption(
            "报废率 > {}% 且连续{}天".format(
        WARNING_THRESHOLD,
        CONSECUTIVE_DAYS))

        st.markdown("---")
        st.caption("⏱️ 实时监控 / 分析报告：每30秒自动刷新")
        st.caption("📝 数据录入：完全手动，不自动刷新")
        st.markdown("---")
        st.caption("{} | {}".format(SYSTEM_NAME, VERSION))
        st.caption("数据存储: SQLite (quality_data.db)")

    return page


# ==================== 主程序 ====================
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


if __name__ == "__main__":
    main()
