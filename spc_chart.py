"""
spc_chart.py - 图表渲染模块
舍弗勒银川工厂 - 车间质量检测跟踪系统
"""
from typing import Optional
from pathlib import Path
import tempfile

import pandas as pd
import plotly.graph_objects as go

# v3.3 新增: 统一配色 (从 config 读取, 兜底内置防报错)
try:
    from config import CHART_COLORS
except Exception:
    CHART_COLORS = {
        "primary": "#1f77b4",
        "success": "#2ca02c",
        "warning": "#ff7f0e",
        "danger": "#d62728",
        "neutral": "#7f7f7f",
    }

# 界面显示的6个缺陷字段名 (与 load_all_records 返回的列名一致, 带斜杠)
DEFECT_FIELDS_DISPLAY = ["锻造缺陷", "磕伤/卡伤", "接刀痕", "过车/欠车", "油沟", "倒角", "漏黑"]


def render_trend_chart(
    df: pd.DataFrame,
    model: str,
    process: Optional[str] = None,
) -> go.Figure:
    """报废率趋势图"""
    filtered = df[df["型号"] == model].copy()
    if process:
        filtered = filtered[filtered["工序"] == process]

    if filtered.empty:
        fig = go.Figure()
        fig.update_layout(
            title="报废率趋势 - {}".format(model),
            annotations=[dict(text="暂无数据", showarrow=False, font=dict(size=16))],
        )
        return fig

    filtered = filtered.sort_values("日期")
    daily = filtered.groupby("日期").agg(
        报废率=("报废率", "mean"),
        检测数量=("检测数量", "sum"),
    ).reset_index()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=daily["日期"],
        y=daily["报废率"],
        mode="lines+markers",
        name="报废率(%)",
        line=dict(color="#dc3545", width=2),
        marker=dict(size=6),
    ))

    fig.add_hline(
        y=5.0,
        line_dash="dash",
        line_color="#ffc107",
        annotation_text="预警阈值 5%",
        annotation_position="top right",
    )

    title = "报废率趋势 - {}".format(model)
    if process:
        title += " | {}".format(process)

    fig.update_layout(
        title=title,
        xaxis_title="日期",
        yaxis_title="报废率 (%)",
        yaxis=dict(rangemode="tozero"),
        height=380,
        margin=dict(l=40, r=20, t=50, b=40),
        hovermode="x unified",
    )
    return fig


def render_pareto_chart(
    df: pd.DataFrame,
    model: Optional[str] = None,
) -> go.Figure:
    """缺陷类型柏拉图 (6个独立缺陷字段汇总)"""
    filtered = df.copy()
    if model:
        filtered = filtered[filtered["型号"] == model]

    if filtered.empty:
        fig = go.Figure()
        fig.update_layout(
            title="缺陷类型柏拉图",
            annotations=[dict(text="暂无数据", showarrow=False, font=dict(size=16))],
        )
        return fig

    defect_fields = DEFECT_FIELDS_DISPLAY
    available_fields = [f for f in defect_fields if f in filtered.columns]

    if not available_fields:
        fig = go.Figure()
        fig.update_layout(
            title="缺陷类型柏拉图",
            annotations=[dict(text="无缺陷类型字段", showarrow=False, font=dict(size=16))],
        )
        return fig

    pareto_rows = []
    for field in available_fields:
        mask = filtered[field].astype(str).str.strip() != ""
        count = int(filtered.loc[mask, "报废数量"].sum())
        if count > 0:
            pareto_rows.append({"缺陷类型": field, "报废数量": count})

    if not pareto_rows:
        fig = go.Figure()
        fig.update_layout(
            title="缺陷类型柏拉图",
            annotations=[dict(text="报废数量为0", showarrow=False, font=dict(size=16))],
        )
        return fig

    pareto_data = pd.DataFrame(pareto_rows).sort_values("报废数量", ascending=False).reset_index(drop=True)
    total = pareto_data["报废数量"].sum()
    pareto_data["占比"] = pareto_data["报废数量"] / total * 100
    pareto_data["累计占比"] = pareto_data["占比"].cumsum()

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=pareto_data["缺陷类型"],
        y=pareto_data["报废数量"],
        name="报废数量",
        marker_color="#4472C4",
        text=pareto_data["报废数量"].astype(str),
        textposition="outside",
    ))
    fig.add_trace(go.Scatter(
        x=pareto_data["缺陷类型"],
        y=pareto_data["累计占比"],
        mode="lines+markers+text",
        name="累计占比(%)",
        yaxis="y2",
        line=dict(color="#dc3545", width=2),
        text=["{:.1f}%".format(v) for v in pareto_data["累计占比"]],
        textposition="top center",
    ))

    fig.update_layout(
        title="缺陷类型柏拉图" + (" - {}".format(model) if model else ""),
        xaxis_title="缺陷类型",
        yaxis=dict(title="报废数量", side="left"),
        yaxis2=dict(title="累计占比 (%)", side="right", overlaying="y", range=[0, 110]),
        height=420,
        margin=dict(l=50, r=50, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig


# ============================================================
# v3.3 图表函数
# ============================================================


def _empty_fig(title, msg="暂无数据"):
    """空数据兜底图 (内部工具函数)"""
    fig = go.Figure()
    fig.update_layout(
        title=title,
        annotations=[dict(text=msg, showarrow=False, font=dict(size=16))],
        height=400,
    )
    return fig


def render_model_pass_rate_chart(df, colors=None):
    """
    图1: 型号合格率组合图 (柱状图+折线图)
    返回 (Figure, 解读文字)
    """
    if colors is None:
        colors = CHART_COLORS
    if df.empty:
        return _empty_fig("型号合格率组合图"), "暂无数据"

    grouped = df.groupby("型号").agg(
        检测数量=("检测数量", "sum"),
        合格数量=("合格数量", "sum"),
    ).reset_index()
    grouped["合格率"] = grouped.apply(
        lambda r: round(r["合格数量"] / r["检测数量"] * 100, 2) if r["检测数量"] > 0 else 0.0, axis=1)
    grouped = grouped.sort_values("合格率", ascending=False)

    if grouped.empty:
        return _empty_fig("型号合格率组合图"), "暂无数据"

    avg_rate = round(grouped["合格率"].mean(), 2)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=grouped["型号"],
        y=grouped["合格率"],
        name="合格率(%)",
        marker_color=colors["primary"],
        text=["{:.1f}%".format(v) for v in grouped["合格率"]],
        textposition="outside",
    ))
    fig.add_trace(go.Scatter(
        x=grouped["型号"],
        y=[avg_rate] * len(grouped),
        mode="lines",
        name="平均值 {:.1f}%".format(avg_rate),
        line=dict(color=colors["warning"], width=2, dash="dash"),
    ))

    fig.update_layout(
        title="型号合格率组合图",
        xaxis_title="型号",
        yaxis_title="合格率 (%)",
        yaxis=dict(rangemode="tozero", range=[0, 110]),
        height=400,
        margin=dict(l=40, r=20, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )

    bottom = grouped.nsmallest(2, "合格率")
    tips = []
    for _, row in bottom.iterrows():
        tips.append("{}({:.1f}%)".format(row["型号"], row["合格率"]))
    insight = "💡 合格率最低: {}，建议排查该型号工序参数与物料稳定性。".format("、".join(tips))
    return fig, insight


def render_defect_analysis_chart(df, colors=None):
    """
    图2: 缺陷类型不良分析组合图 (柱状图+折线图)
    返回 (Figure, Top3明细DataFrame, 解读文字)
    """
    if colors is None:
        colors = CHART_COLORS
    if df.empty:
        return _empty_fig("缺陷类型不良分析组合图"), pd.DataFrame(), "暂无数据"

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
        occur = len(sub)
        rows.append({"缺陷类型": field, "不良数量": bad_qty, "涉及型号数": model_count, "发生次数": occur})

    if not rows:
        return _empty_fig("缺陷类型不良分析组合图", "无缺陷数据"), pd.DataFrame(), "无缺陷数据"

    data = pd.DataFrame(rows).sort_values("不良数量", ascending=False).reset_index(drop=True)
    total = data["不良数量"].sum()
    data["占比"] = data["不良数量"].apply(lambda x: round(x / total * 100, 1) if total > 0 else 0.0)
    data["累计占比"] = data["占比"].cumsum()

    bar_colors = []
    for i in range(len(data)):
        if i < 2:
            bar_colors.append(colors["danger"])
        elif i < 4:
            bar_colors.append(colors["warning"])
        else:
            bar_colors.append(colors["neutral"])

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=data["缺陷类型"],
        y=data["不良数量"],
        name="不良数量",
        marker_color=bar_colors,
        text=data["不良数量"].astype(str),
        textposition="outside",
    ))
    fig.add_trace(go.Scatter(
        x=data["缺陷类型"],
        y=data["累计占比"],
        mode="lines+markers+text",
        name="累计占比(%)",
        yaxis="y2",
        line=dict(color=colors["danger"], width=2),
        text=["{:.1f}%".format(v) for v in data["累计占比"]],
        textposition="top center",
    ))
    fig.update_layout(
        title="缺陷类型不良分析组合图",
        xaxis_title="缺陷类型",
        yaxis=dict(title="不良数量", side="left"),
        yaxis2=dict(title="累计占比 (%)", side="right", overlaying="y", range=[0, 110]),
        height=400,
        margin=dict(l=50, r=50, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )

    top3 = data.head(3)[["缺陷类型", "占比", "涉及型号数", "发生次数"]].copy()
    top1 = data.iloc[0]
    insight = "💡 主要缺陷: {}({:.1f}%)，涉及{}个型号，建议优先制定纠正措施。".format(
        top1["缺陷类型"], top1["占比"], top1["涉及型号数"])
    return fig, top3, insight


def render_top5_alert_chart(df, days, threshold, colors=None):
    """
    图3: 近N天不良Top5预警看板 (柱状图+折线图组合, 图1的下钻视角)
    - 柱状图: 报废数量 (主色蓝)
    - 折线图: 报废率(%) (异常红)
    - 横轴: 满足条件的型号, 按报废数量降序
    返回 (Figure, Top5明细DataFrame, 解读文字)
    """
    if colors is None:
        colors = CHART_COLORS
    if df.empty:
        return _empty_fig("近{}天不良Top5预警看板".format(days)), pd.DataFrame(), "暂无数据"

    df = df.copy()
    df["日期_dt"] = pd.to_datetime(df["日期"], errors="coerce")
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
    recent = df[df["日期_dt"] >= cutoff].copy()
    if recent.empty:
        return _empty_fig("近{}天不良Top5预警看板".format(days)), pd.DataFrame(), "所选时间范围内无数据"

    grouped = recent.groupby("型号").agg(
        检测数量=("检测数量", "sum"),
        报废数量=("报废数量", "sum"),
    ).reset_index()
    grouped = grouped[grouped["报废数量"] >= threshold]
    if grouped.empty:
        return _empty_fig("近{}天不良Top5预警看板".format(days)), pd.DataFrame(), "当前条件下无符合型号"

    grouped["报废率"] = grouped.apply(
        lambda r: round(r["报废数量"] / r["检测数量"] * 100, 2) if r["检测数量"] > 0 else 0.0, axis=1)
    grouped = grouped.sort_values("报废数量", ascending=False).reset_index(drop=True)

    # ★ 新增: 主要缺陷类型
    main_defects = []
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
            top_d = max(defect_qty, key=defect_qty.get)
            main_defects.append(top_d)
        else:
            main_defects.append("-")
    grouped["主要缺陷"] = main_defects

    # ★ 图表: 柱状图(报废数量) + 折线图(报废率), 与图1风格一致
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=grouped["型号"],
        y=grouped["报废数量"],
        name="报废数量",
        marker_color=colors["primary"],
        text=grouped["报废数量"].astype(str),
        textposition="outside",
    ))
    fig.add_trace(go.Scatter(
        x=grouped["型号"],
        y=grouped["报废率"],
        mode="lines+markers+text",
        name="报废率(%)",
        yaxis="y2",
        line=dict(color=colors["danger"], width=2),
        text=["{:.1f}%".format(v) for v in grouped["报废率"]],
        textposition="top center",
    ))
    fig.update_layout(
        title="近{}天不良Top5预警看板 (阈值≥{}件)".format(days, threshold),
        xaxis_title="型号",
        yaxis=dict(title="报废数量", side="left"),
        yaxis2=dict(title="报废率 (%)", side="right", overlaying="y", range=[0, 110]),
        height=400,
        margin=dict(l=50, r=50, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )

    # Top5明细 (不足5个则全部显示)
    top5 = grouped.head(5)
    detail = top5[["型号", "检测数量", "报废数量", "报废率", "主要缺陷"]].copy()

    # 解读文字
    total_models = len(grouped)
    top1 = top5.iloc[0]
    insight = "💡 近{}天报废≥{}件的型号共{}个，最严重的是{}型号，报废{}件，主要缺陷为{}。".format(
        days, threshold, total_models, top1["型号"], int(top1["报废数量"]), top1["主要缺陷"])
    return fig, detail, insight


def render_monthly_trend_chart(df, model, defect_type, months=6, colors=None):
    """
    图4: 型号缺陷模式月度趋势图 (按月份聚合, 连续折线)
    - months: 近多少个月 (3/6/12), 默认6
    - defect_type="全部"时: 多折线, 主线主色, 其余灰
    - defect_type具体时: 单折线
    - 数据不依赖外部日期筛选, 从完整df中按月份范围自行筛选
    返回 (Figure, 解读文字)
    """
    if colors is None:
        colors = CHART_COLORS
    if df.empty:
        return _empty_fig("型号缺陷模式月度趋势图"), "暂无数据"

    df = df.copy()
    df["日期_dt"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期_dt"])
    df["月份"] = df["日期_dt"].dt.strftime("%Y-%m")

    # ★ 按月份范围筛选 (不依赖外部日期)
    all_months = sorted(df["月份"].unique())
    recent_months = all_months[-months:] if len(all_months) >= months else all_months
    df = df[df["月份"].isin(recent_months)]

    if model != "全部":
        df = df[df["型号"] == model]
    if df.empty:
        return _empty_fig("型号缺陷模式月度趋势图"), "该型号无数据"

    fig = go.Figure()

    if defect_type == "全部":
        # ★ 固定显示6种缺陷类型, 全部画线, 数据为0也显示 (修复: 不再隐藏后3条)
        # 每种缺陷类型固定一个颜色, 顺序与DEFECT_FIELDS_DISPLAY一致
        field_colors = {
            "锻造缺陷": "#9467bd",   # 紫
            "磕伤/卡伤": "#d62728",  # 红
            "接刀痕": "#ff7f0e",     # 橙
            "过车/欠车": "#1f77b4",  # 蓝
            "油沟": "#2ca02c",       # 绿
            "倒角": "#8c564b",       # 棕
            "漏黑": "#2c2c2c",       # 深灰/黑
        }

        for field in DEFECT_FIELDS_DISPLAY:
            # ★ 不跳过任何字段, 全部画线
            if field in df.columns:
                mask = df[field].astype(str).str.strip() != ""
                monthly = df[mask].groupby("月份")["报废数量"].sum().reindex(recent_months, fill_value=0).reset_index()
                monthly = monthly.sort_values("月份")
            else:
                # 字段不存在, 全0直线
                monthly = pd.DataFrame({"月份": recent_months, "报废数量": [0] * len(recent_months)})
            fig.add_trace(go.Scatter(
                x=monthly["月份"],
                y=monthly["报废数量"],
                mode="lines+markers",
                name=field,
                line=dict(color=field_colors.get(field, "#7f7f7f"), width=2.5),
                visible=True,  # ★ 全部显示, 不再隐藏
                connectgaps=True,
            ))
    else:
        if defect_type not in df.columns:
            return _empty_fig("型号缺陷模式月度趋势图"), "无该缺陷字段"
        mask = df[defect_type].astype(str).str.strip() != ""
        # ★ 按月份聚合, reindex确保所有月份都有点
        monthly = df[mask].groupby("月份")["报废数量"].sum().reindex(recent_months, fill_value=0).reset_index()
        monthly = monthly.sort_values("月份")
        fig.add_trace(go.Scatter(
            x=monthly["月份"],
            y=monthly["报废数量"],
            mode="lines+markers+text",
            name=defect_type,
            line=dict(color=colors["primary"], width=3),
            text=monthly["报废数量"].astype(str),
            textposition="top center",
            connectgaps=True,
        ))

    fig.update_layout(
        title="型号缺陷模式月度趋势图" + (" - {}".format(model) if model != "全部" else "") + " (近{}个月)".format(months),
        xaxis_title="月份",
        yaxis_title="不良数量",
        height=450,
        margin=dict(l=40, r=20, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        xaxis=dict(categoryorder="category ascending"),  # ★ X轴按时间先后排列
    )

    # ★ 环比解读
    if len(recent_months) >= 2:
        m_cur, m_prev = recent_months[-1], recent_months[-2]
        if defect_type == "全部":
            cur_total = int(df[df["月份"] == m_cur]["报废数量"].sum())
            prev_total = int(df[df["月份"] == m_prev]["报废数量"].sum())
        else:
            mask_cur = (df["月份"] == m_cur) & (df[defect_type].astype(str).str.strip() != "")
            mask_prev = (df["月份"] == m_prev) & (df[defect_type].astype(str).str.strip() != "")
            cur_total = int(df.loc[mask_cur, "报废数量"].sum())
            prev_total = int(df.loc[mask_prev, "报废数量"].sum())

        if prev_total > 0:
            change = round((cur_total - prev_total) / prev_total * 100, 1)
            if change > 0:
                insight = "💡 {}月环比{}月上升{}%，不良数增加，提示质量风险，建议加强管控。".format(m_cur, m_prev, change)
            elif change < 0:
                insight = "💡 {}月环比{}月下降{}%，改善措施见效，建议继续保持。".format(m_cur, m_prev, abs(change))
            else:
                insight = "💡 {}月与{}月持平，质量态势稳定。".format(m_cur, m_prev)
        else:
            insight = "💡 {}月不良数{}件，上月无数据对比。".format(m_cur, cur_total)
    else:
        insight = "💡 仅有1个月数据，暂无法做环比分析。"

    return fig, insight


def render_monthly_pass_rate_trend_chart(df, colors=None):
    """
    月报用: 月度合格率趋势图 (合格率+报废率双折线)
    返回 Figure
    """
    if colors is None:
        colors = CHART_COLORS
    if df.empty:
        return _empty_fig("月度合格率趋势图")

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
    monthly = monthly.sort_values("月份").tail(3)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=monthly["月份"], y=monthly["合格率"],
        mode="lines+markers+text", name="合格率(%)",
        line=dict(color=colors["success"], width=3),
        text=["{:.1f}%".format(v) for v in monthly["合格率"]],
        textposition="top center",
        connectgaps=True,
    ))
    fig.add_trace(go.Scatter(
        x=monthly["月份"], y=monthly["报废率"],
        mode="lines+markers+text", name="报废率(%)",
        line=dict(color=colors["danger"], width=3),
        text=["{:.1f}%".format(v) for v in monthly["报废率"]],
        textposition="bottom center",
        connectgaps=True,
    ))
    fig.update_layout(
        title="月度合格率趋势图 (近3个月)",
        xaxis_title="月份", yaxis_title="百分比 (%)",
        height=380, margin=dict(l=40, r=20, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        xaxis=dict(categoryorder="category ascending"),
    )
    return fig
