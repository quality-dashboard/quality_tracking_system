"""
spc_chart.py - 图表渲染模块
舍弗勒银川工厂 - 车间质量检测跟踪系统
"""
from typing import Optional
from pathlib import Path
import tempfile

import streamlit as st
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

# 界面显示的缺陷字段名 (与 load_all_records 返回的列名一致, 带斜杠的带斜杠)
# 7个原字段 + 5个新增字段(椭圆超差/内径垫伤/滚道对称点过大/对称度超差/幅高椭圆)
DEFECT_FIELDS_DISPLAY = ["锻造缺陷", "磕伤/卡伤", "接刀痕", "过车/欠车", "油沟", "倒角", "漏黑",
                         "椭圆超差", "内径垫伤", "滚道对称点过大", "对称度超差", "幅高椭圆"]

# ★ v3.4: 12种缺陷类型的固定配色 (堆叠总览图/月度趋势图共用, 保证同一缺陷在所有图里颜色一致)
DEFECT_COLORS = {
    "锻造缺陷": "#9467bd",       # 紫
    "磕伤/卡伤": "#d62728",      # 红
    "接刀痕": "#ff7f0e",         # 橙
    "过车/欠车": "#1f77b4",      # 蓝
    "油沟": "#2ca02c",           # 绿
    "倒角": "#8c564b",           # 棕
    "漏黑": "#2c2c2c",           # 深灰/黑
    "椭圆超差": "#17becf",       # 青
    "内径垫伤": "#e377c2",       # 粉红
    "滚道对称点过大": "#393b79",  # 深蓝紫
    "对称度超差": "#637ed6",     # 蓝紫
    "幅高椭圆": "#8c6d31",       # 橄榄棕
}


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


@st.cache_data(show_spinner=False)
def render_model_fail_rate_chart(df, colors=None):
    """
    图1(改造版): 型号不合格率问题看板 (柱状图+折线图)
    - 只展示不合格率 > 5% 的型号 (聚焦问题型号)
    - 柱状图(左Y): 不合格数量 = 报废数量 + 返修数量
    - 折线图(右Y): 不合格率 (%)
    - 横轴: 按不合格率从高到低排序 (最严重的在左)
    - 空值保持为空, 检测数量为0的型号不合格率置0(会被>5%筛掉)
    返回 (Figure, 解读文字)
    """
    if colors is None:
        colors = CHART_COLORS
    title = "型号不合格率问题看板（不合格率>5%）"
    if df.empty:
        return _empty_fig(title), "暂无数据"

    grouped = df.groupby("型号").agg(
        检测数量=("检测数量", "sum"),
        报废数量=("报废数量", "sum"),
        返修数量=("返修数量", "sum"),
    ).reset_index()

    # 不合格数量 = 报废 + 返修; 不合格率 = 不合格数量 / 检测数量 × 100
    grouped["不合格数量"] = grouped["报废数量"] + grouped["返修数量"]
    grouped["不合格率"] = grouped.apply(
        lambda r: round(r["不合格数量"] / r["检测数量"] * 100, 2) if r["检测数量"] and r["检测数量"] > 0 else 0.0,
        axis=1,
    )

    # 只保留不合格率 > 5% 的型号
    problem = grouped[grouped["不合格率"] > 5.0].copy()
    if problem.empty:
        msg = "当前时间范围内无不合格率超过5%的型号"
        return _empty_fig(title, msg), msg

    # 按不合格率从高到低 (问题最严重的在左)
    problem = problem.sort_values("不合格率", ascending=False).reset_index(drop=True)

    # 悬停数据: [检测数量, 不合格数量, 不合格率] (柱/折线统一, 鼠标放哪都看全)
    hover_data = problem[["检测数量", "不合格数量", "不合格率"]].values.tolist()
    hover_tpl = "<b>%{x}</b>｜检测数量：%{customdata[0]:.0f}｜不合格数量：%{customdata[1]:.0f}｜不合格率：%{customdata[2]:.1f}%<extra></extra>"

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=problem["型号"],
        y=problem["不合格数量"],
        name="不合格数量",
        marker_color=colors["danger"],
        text=problem["不合格数量"].astype(str),
        textposition="outside",
        customdata=hover_data,
        hovertemplate=hover_tpl,
    ))
    fig.add_trace(go.Scatter(
        x=problem["型号"],
        y=problem["不合格率"],
        mode="lines+markers+text",
        name="不合格率(%)",
        yaxis="y2",
        line=dict(color=colors["warning"], width=2),
        text=["{:.1f}%".format(v) for v in problem["不合格率"]],
        textposition="top center",
        customdata=hover_data,
        hovertemplate=hover_tpl,
    ))
    fig.update_layout(
        title=title,
        xaxis_title="型号",
        yaxis=dict(title="不合格数量", side="left", rangemode="tozero"),
        yaxis2=dict(title="不合格率 (%)", side="right", overlaying="y", rangemode="tozero"),
        height=400,
        margin=dict(l=50, r=50, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )

    # 解读: 不合格率最高的1-2个型号
    top_n = problem.head(2)
    tips = ["{}({:.1f}%)".format(r["型号"], r["不合格率"]) for _, r in top_n.iterrows()]
    insight = "💡 不合格率最高的型号: {}，建议优先排查该型号工序能力与物料稳定性。".format("、".join(tips))
    return fig, insight


@st.cache_data(show_spinner=False)
def render_defect_stack_chart(df, colors=None):
    """
    ★ v3.4 分析报告第一张图: 问题型号缺陷堆叠总览图 (不良率>5%)
    - 早会投屏第一眼: 哪些型号有问题、问题多大、主要由什么缺陷导致
    - 不良率口径: (报废数量+返修数量)/检测数量×100, 与图1"不合格率"一致
    - X轴: 不良率>5%的型号, 按不良率从高到低排列 (最严重的在左)
    - 柱子总高 = 该型号当前时间范围内不良总数(报废+返修)
    - 柱内按缺陷类型堆叠(12种固定缺陷全量进图例, 数据为0也显示),
      ★ v3.8 缺陷段数量=缺陷字段值本身(该类的报废+返修), 另加"其他不良(未填缺陷)"兜底段
    - 柱顶标注该型号不良率(%), 悬停显示完整明细
    返回 (Figure, 解读文字)
    """
    if colors is None:
        colors = CHART_COLORS
    title = "问题型号缺陷堆叠总览图（不良率>5%）"
    if df.empty:
        return _empty_fig(title), "暂无数据"

    # ---------- 1) 按型号聚合检测/报废/返修, 计算不良率 ----------
    grouped = df.groupby("型号").agg(
        检测数量=("检测数量", "sum"),
        报废数量=("报废数量", "sum"),
        返修数量=("返修数量", "sum"),
    ).reset_index()
    grouped["不良数量"] = grouped["报废数量"] + grouped["返修数量"]
    grouped["不良率"] = grouped.apply(
        lambda r: round(r["不良数量"] / r["检测数量"] * 100, 2) if r["检测数量"] and r["检测数量"] > 0 else 0.0,
        axis=1,
    )

    # ---------- 2) 只保留不良率>5%的问题型号, 按不良率从高到低 ----------
    problem = grouped[grouped["不良率"] > 5.0].copy()
    if problem.empty:
        msg = "当前时间范围内无不良率超过5%的型号"
        return _empty_fig(title, msg), "💡 " + msg + "，整体质量状况良好。"
    problem = problem.sort_values("不良率", ascending=False).reset_index(drop=True)
    models = problem["型号"].tolist()
    problem_idx = problem.set_index("型号")

    # ---------- 3) 统计每个问题型号各缺陷类型的不良数量 ----------
    # ★ v3.8 统一口径: 缺陷字段值本身 = 该缺陷类型的不良数量(该类的报废+返修)
    #   与图2/图4/超标缺陷汇总图完全一致; 不再用"字段非空→计整条记录报废"的旧口径,
    #   否则多字段记录会重复计算(如字段 过车1+漏黑1, 会被各计整条记录的29件)
    defect_qty = {}  # {缺陷类型: {型号: 不良数量(字段值)}}
    for field in DEFECT_FIELDS_DISPLAY:
        if field in df.columns:
            vals = pd.to_numeric(df[field], errors="coerce").fillna(0)
            sub = df.assign(_v=vals).groupby("型号")["_v"].sum()
            defect_qty[field] = {m: int(sub.get(m, 0)) for m in models}
        else:
            # 字段不存在时全0 (仍进图例, 保证12种缺陷类型完整展示)
            defect_qty[field] = {m: 0 for m in models}

    # ---------- 4) 兜底段: 其他不良(未填缺陷类型的报废/返修) ----------
    # 字段值已包含该类的不良(报废+返修), 未填缺陷的部分 = 不良总数 - 字段值合计
    # (负数按0兜底, 防个别脏数据让堆叠段反向画)
    classified = {m: sum(defect_qty[f][m] for f in DEFECT_FIELDS_DISPLAY) for m in models}
    other_bad = {m: max(0, int(problem_idx.loc[m, "不良数量"]) - classified[m]) for m in models}

    # ---------- 5) 画堆叠柱 (每种缺陷一个Bar段, barmode=stack) ----------
    fig = go.Figure()
    for field in DEFECT_FIELDS_DISPLAY:
        y = [defect_qty[field][m] for m in models]
        fig.add_trace(go.Bar(
            x=models, y=y, name=field,
            marker_color=DEFECT_COLORS.get(field, "#7f7f7f"),
            hovertemplate="<b>%{x}</b>｜%{fullData.name}：%{y:.0f}件<extra></extra>",
        ))
    # 其他不良段: 未填缺陷类型的报废/返修, 只有>0才画 (取代原"其他报废+返修"两段)
    # ★ v3.8: 字段值已含该类的返修部分, 返修不再单独一段, 未归类的统一进"其他不良"
    if any(v > 0 for v in other_bad.values()):
        fig.add_trace(go.Bar(
            x=models, y=[other_bad[m] for m in models],
            name="其他不良(未填缺陷)", marker_color="#b0b0b0",
            hovertemplate="<b>%{x}</b>｜其他不良(未填缺陷)：%{y:.0f}件<extra></extra>",
        ))

    # ---------- 6) 柱顶标注不良率(%) (隐形点位+文字, 不遮挡堆叠) ----------
    fig.add_trace(go.Scatter(
        x=models,
        y=problem["不良数量"].tolist(),
        mode="markers+text",
        name="不良率",
        showlegend=False,
        marker=dict(size=1, opacity=0),
        text=["{:.1f}%".format(v) for v in problem["不良率"]],
        textposition="top center",
        textfont=dict(size=14, color=colors["danger"]),
        hovertemplate="<b>%{x}</b>｜不良率：%{text}<extra></extra>",
    ))

    # ★ Y轴留12%顶部余量: 给柱顶的不良率%标注留显示空间, 不被裁剪
    y_max = float(problem["不良数量"].max())

    fig.update_layout(
        title=title,
        xaxis_title="型号（按不良率从高到低）",
        yaxis_title="不良总数（件）= 报废 + 返修",
        barmode="stack",
        height=660,  # ★ v3.5 增大整体高度, 给底部图例留出独立空间(适合早会投屏)
        margin=dict(l=60, r=20, t=80, b=170),  # ★ 顶部80防不良率标注被裁, 底部170专门留给图例
        # ★ v3.5 图例移到图表下方独立区域: 12+项图例横向自动换行成多行, 不再与标题重叠
        legend=dict(orientation="h", yanchor="top", y=-0.18, x=0, xanchor="left"),
        yaxis=dict(rangemode="tozero", range=[0, y_max * 1.12]),
        hovermode="x unified",
    )

    # ---------- 7) 解读: 最严重型号 + 其主要缺陷构成 ----------
    top1 = problem.iloc[0]
    top1_defects = {f: defect_qty[f][top1["型号"]] for f in DEFECT_FIELDS_DISPLAY}
    main_defect = max(top1_defects, key=top1_defects.get) if top1_defects else "无"
    insight = "💡 不良率>5%的型号共{}个，最严重的是{}型号（不良率{:.1f}%，不良{}件），其主要缺陷为{}，建议早会重点跟进。".format(
        len(problem), top1["型号"], top1["不良率"], int(top1["不良数量"]), main_defect)
    return fig, insight


@st.cache_data(show_spinner=False)
def render_overlimit_defect_chart(df, colors=None):
    """
    ★ v3.7 新增: 超标缺陷类型汇总图（不良率>1%）
    - 组合图: 柱状图(不良数量, 左轴) + 折线图(不良率%, 右轴)
    - 不良率口径: (报废数量+返修数量) ÷ 检测数量 × 100%
      归集方式: 缺陷字段值本身=该类型不良数量(诊断确认字段值是逐类分解,
      164/170条记录满足字段值合计=报废+返修; 不能按"字段非空计整条记录", 会重复计算)
    - 只显示不良率>1%的缺陷类型; X轴按不良数量从高到低排列
    - 时间范围由调用方(app.py)通过df传入(跟随顶部开始/结束日期筛选)
    - 无达标缺陷时显示"当前无不良率超过1%的缺陷类型", 不报错
    返回 (Figure, 解读文字)
    """
    if colors is None:
        colors = CHART_COLORS
    title = "超标缺陷类型汇总图（不良率>1%）"

    if df.empty:
        return _empty_fig(title), "暂无数据"

    total_inspect = int(df["检测数量"].sum())
    if total_inspect <= 0:
        return _empty_fig(title, "当前无不良率超过1%的缺陷类型"), "💡 当前无不良率超过1%的缺陷类型。"

    # ---------- 1) 逐缺陷统计不良数量与不良率, 筛出>1% ----------
    # ★ 归集口径: 缺陷字段值本身 = 该缺陷类型的不良数量(该类的报废+返修)
    #   真实数据诊断确认: 164/170条有缺陷字段的记录满足"字段值合计=报废+返修",
    #   字段值是逐类分解的数量; 若按"字段非空→计整条记录报废+返修"归集,
    #   46条多字段记录会重复计算(如某记录字段值 过车1+漏黑1=2, 却被各计29)
    rows = []
    for field in DEFECT_FIELDS_DISPLAY:
        if field not in df.columns:
            continue
        vals = pd.to_numeric(df[field], errors="coerce").fillna(0)
        bad_qty = int(vals.sum())
        bad_rate = bad_qty / total_inspect * 100
        if bad_rate > 1.0:
            rows.append({"缺陷类型": field, "不良数量": bad_qty, "不良率": round(bad_rate, 2)})

    if not rows:
        # ★ 筛选后无超标缺陷: 显示提示文字, 不报错
        return _empty_fig(title, "当前无不良率超过1%的缺陷类型"), "💡 当前无不良率超过1%的缺陷类型，整体质量状况良好。"

    # ---------- 2) 按不良数量从高到低排列 ----------
    data = pd.DataFrame(rows).sort_values("不良数量", ascending=False).reset_index(drop=True)

    # ---------- 3) 组合图: 柱(不良数量, 左轴) + 折线(不良率%, 右轴) ----------
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=data["缺陷类型"], y=data["不良数量"],
        name="不良数量(报废+返修)",
        marker_color=colors["primary"],
        text=data["不良数量"].astype(str),
        textposition="outside",
        hovertemplate="<b>%{x}</b>｜不良数量：%{y}件<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=data["缺陷类型"], y=data["不良率"],
        name="不良率(%)", yaxis="y2",
        mode="lines+markers+text",
        line=dict(color=colors["danger"], width=3),
        marker=dict(size=8),
        text=["{:.2f}%".format(v) for v in data["不良率"]],
        textposition="top center",
        hovertemplate="<b>%{x}</b>｜不良率：%{y:.2f}%<extra></extra>",
    ))

    # 左轴留15%顶部余量, 防柱顶数值标注被裁剪
    y_max = float(data["不良数量"].max())
    fig.update_layout(
        title=title,
        xaxis_title="缺陷类型（按不良数量从高到低）",
        yaxis=dict(title="不良数量（件）", side="left", rangemode="tozero", range=[0, y_max * 1.15]),
        yaxis2=dict(title="不良率 (%)", side="right", overlaying="y", rangemode="tozero"),
        height=460,  # ★ 单张全宽大图, 不显小
        margin=dict(l=60, r=60, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        hovermode="x unified",
    )

    # ---------- 4) 自动解读: 指出不良率最高/数量最多的1-2个缺陷类型 ----------
    top_qty_row = data.iloc[0]  # 已按不良数量降序, 首行即数量最多
    top_rate_row = data.sort_values("不良率", ascending=False).iloc[0]
    if top_qty_row["缺陷类型"] == top_rate_row["缺陷类型"]:
        insight = "💡 {}同时是不良数量最多（{}件）且不良率最高（{:.2f}%）的缺陷类型，建议优先整改。".format(
            top_qty_row["缺陷类型"], int(top_qty_row["不良数量"]), top_rate_row["不良率"])
    else:
        insight = "💡 不良数量最多的是{}（{}件），不良率最高的是{}（{:.2f}%），两者建议优先整改。".format(
            top_qty_row["缺陷类型"], int(top_qty_row["不良数量"]),
            top_rate_row["缺陷类型"], top_rate_row["不良率"])
    return fig, insight


@st.cache_data(show_spinner=False)
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
        # ★ v3.8 统一口径: 不良数量 = 缺陷字段值本身(该类的报废+返修)
        #   不再用"字段非空→计整条记录报废"归集(多字段记录会重复计算)
        bad_qty = int(pd.to_numeric(sub[field], errors="coerce").fillna(0).sum())
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


@st.cache_data(show_spinner=False)
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
    # v3.8 统一口径: 缺陷数量 = 字段值本身(该类的报废+返修), 与图2/图4/堆叠图一致
    main_defects = []
    for model in grouped["型号"]:
        sub = recent[recent["型号"] == model]
        defect_qty = {}
        for field in DEFECT_FIELDS_DISPLAY:
            if field not in sub.columns:
                continue
            q = int(pd.to_numeric(sub[field], errors="coerce").fillna(0).sum())
            if q > 0:
                defect_qty[field] = q
        if defect_qty:
            top_d = max(defect_qty, key=defect_qty.get)
            main_defects.append(top_d)
        else:
            main_defects.append("-")
    grouped["主要缺陷"] = main_defects

    # 悬停数据: [检测数量, 报废数量, 报废率] (柱/折线统一, 与图1同款交互)
    hover_data = grouped[["检测数量", "报废数量", "报废率"]].values.tolist()
    hover_tpl = "<b>%{x}</b>｜检测数量：%{customdata[0]:.0f}｜报废数量：%{customdata[1]:.0f}｜报废率：%{customdata[2]:.1f}%<extra></extra>"

    # ★ 图表: 柱状图(报废数量) + 折线图(报废率), 与图1风格一致
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=grouped["型号"],
        y=grouped["报废数量"],
        name="报废数量",
        marker_color=colors["primary"],
        text=grouped["报废数量"].astype(str),
        textposition="outside",
        customdata=hover_data,
        hovertemplate=hover_tpl,
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
        customdata=hover_data,
        hovertemplate=hover_tpl,
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


@st.cache_data(show_spinner=False)
def render_monthly_trend_chart(df, model, defect_type, months=6, colors=None):
    """
    图4: 型号缺陷模式月度趋势图 (按月份聚合, 连续折线)
    ★ v3.4: 纵轴从"不良数量"改为"不良率(%)"
    ★ v3.5: "全部"模式改为显示不良率最高的Top5缺陷类型(不再用>1%硬过滤, 避免图表空白)
    - 不良率口径: 该月该缺陷字段值合计(该类的报废+返修) / 该月检测数量 × 100
      ★ v3.8 统一"缺陷字段值口径", 与图2/堆叠图/超标缺陷汇总图完全一致
    - Top5排名口径: 近N个月累计口径(该缺陷字段值累计/累计检测), 避免月度波动导致排名跳动
    - defect_type具体时: 单折线(用户主动指定, 不受Top5筛选)
    - 时间范围保持手动可选 (近3/6/12个月)
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

    # ★ 每月检测数量 = 不良率的分母 (按当前型号范围聚合)
    monthly_inspect = df.groupby("月份")["检测数量"].sum().reindex(recent_months, fill_value=0)
    total_inspect = int(monthly_inspect.sum())

    def _month_rates(field):
        """某缺陷各月不良率(%)列表; 分母为0的月份无意义, 置None不画点
        ★ v3.8 统一口径: 分子 = 该缺陷字段值按月合计(该类的报废+返修)"""
        if field not in df.columns:
            return [None] * len(recent_months)
        vals = pd.to_numeric(df[field], errors="coerce").fillna(0)
        monthly_bad = df.assign(_v=vals).groupby("月份")["_v"].sum().reindex(recent_months, fill_value=0)
        rates = []
        for m in recent_months:
            insp = monthly_inspect.get(m, 0)
            rates.append(round(monthly_bad.get(m, 0) / insp * 100, 2) if insp and insp > 0 else None)
        return rates

    fig = go.Figure()
    top5_fields = []   # "全部"模式下记录Top5缺陷, 用于标题/解读标注
    top5_label = ""

    if defect_type == "全部":
        # ★ v3.5: 显示不良率最高的Top5缺陷 (不再用>1%硬过滤, 避免所有缺陷都低于1%时图表空白)
        # 排名口径: 近N个月累计不良数(缺陷字段值合计) / 累计检测数量 = 整体不良率
        if total_inspect <= 0:
            return _empty_fig("型号缺陷模式月度趋势图", "该时间范围内暂无缺陷数据"), "💡 该时间范围内暂无缺陷数据。"

        field_stats = []  # [(缺陷类型, 整体不良率, 累计不良数)]
        for field in DEFECT_FIELDS_DISPLAY:
            if field in df.columns:
                # ★ v3.8 统一口径: 累计不良数 = 缺陷字段值合计(该类的报废+返修)
                field_bad = int(pd.to_numeric(df[field], errors="coerce").fillna(0).sum())
                if field_bad > 0:  # 有不良数据才参与排名
                    field_stats.append((field, round(field_bad / total_inspect * 100, 2), field_bad))

        if not field_stats:
            # ★ 确实完全没有缺陷数据时, 才显示空白提示
            return _empty_fig("型号缺陷模式月度趋势图", "该时间范围内暂无缺陷数据"), "💡 该时间范围内暂无缺陷数据。"

        # 按整体不良率降序取Top5 (不足5个就全部显示)
        field_stats.sort(key=lambda x: (x[1], x[2]), reverse=True)
        top5_fields = [f for f, _, _ in field_stats[:5]]
        top5_label = "、".join(top5_fields)

        # 画Top5缺陷类型的不良率折线 (颜色与堆叠总览图一致)
        for field in top5_fields:
            fig.add_trace(go.Scatter(
                x=recent_months,
                y=_month_rates(field),
                mode="lines+markers",
                name=field,
                line=dict(color=DEFECT_COLORS.get(field, "#7f7f7f"), width=2.5),
                connectgaps=True,  # 分母为0的月份断点自动连线
                hovertemplate="<b>%{x}</b>｜%{fullData.name}不良率：%{y:.2f}%<extra></extra>",
            ))
    else:
        if defect_type not in df.columns:
            return _empty_fig("型号缺陷模式月度趋势图"), "无该缺陷字段"
        # ★ 单缺陷: 月度不良率折线, 数值标注在点上方
        rates = _month_rates(defect_type)
        fig.add_trace(go.Scatter(
            x=recent_months,
            y=rates,
            mode="lines+markers+text",
            name=defect_type,
            line=dict(color=DEFECT_COLORS.get(defect_type, colors["primary"]), width=3),
            text=[("{:.2f}%".format(v) if v is not None else "") for v in rates],
            textposition="top center",
            connectgaps=True,
            hovertemplate="<b>%{{x}}</b>｜{}不良率：%{{y:.2f}}%<extra></extra>".format(defect_type),
        ))

    # ★ 标题: "全部"模式下标注当前显示的是Top5不良率缺陷
    chart_title = "型号缺陷模式月度趋势图" + (" - {}".format(model) if model != "全部" else "") + " (近{}个月)".format(months)
    if defect_type == "全部":
        chart_title += " - 不良率Top5缺陷"

    fig.update_layout(
        title=chart_title,
        xaxis_title="月份",
        yaxis_title="不良率（%）",
        height=450,
        margin=dict(l=40, r=20, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        xaxis=dict(categoryorder="category ascending"),  # ★ X轴按时间先后排列
        yaxis=dict(rangemode="tozero"),
    )

    # ★ 环比解读 (v3.4: 基于不良率, 当月 vs 上月, 单位: 百分点)
    if len(recent_months) >= 2:
        m_cur, m_prev = recent_months[-1], recent_months[-2]

        def _month_rate(m):
            """某月不良率(%): "全部"时为整体口径, 指定缺陷时为该缺陷口径
            ★ v3.8 统一口径: 指定缺陷时分子 = 该缺陷字段值当月合计(该类的报废+返修)"""
            insp = monthly_inspect.get(m, 0)
            if not insp or insp <= 0:
                return None
            if defect_type == "全部":
                scrap = int(df[df["月份"] == m]["报废数量"].sum())
            else:
                m_vals = pd.to_numeric(df.loc[df["月份"] == m, defect_type], errors="coerce").fillna(0)
                scrap = int(m_vals.sum())
            return round(scrap / insp * 100, 2)

        cur_rate = _month_rate(m_cur)
        prev_rate = _month_rate(m_prev)
        scope = "整体不良率" if defect_type == "全部" else "{}不良率".format(defect_type)

        if cur_rate is None:
            insight = "💡 {}月无有效检测数据，暂无法做环比分析。".format(m_cur)
        elif prev_rate is None:
            insight = "💡 {}月{}为{:.2f}%，上月无有效数据对比。".format(m_cur, scope, cur_rate)
        else:
            diff = round(cur_rate - prev_rate, 2)
            if diff > 0:
                insight = "💡 {}月{}环比{}月上升{}个百分点，提示质量风险，建议加强管控。".format(m_cur, scope, m_prev, diff)
            elif diff < 0:
                insight = "💡 {}月{}环比{}月下降{}个百分点，改善措施见效，建议继续保持。".format(m_cur, scope, m_prev, abs(diff))
            else:
                insight = "💡 {}月{}与{}月持平，质量态势稳定。".format(m_cur, scope, m_prev)
    else:
        insight = "💡 仅有1个月数据，暂无法做环比分析。"

    # ★ v3.5: "全部"模式下在解读里明确标注显示的是哪Top5个缺陷
    if defect_type == "全部" and top5_fields:
        insight = "💡 不良率Top5缺陷：{}。{}".format(top5_label, insight.replace("💡 ", "", 1))

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


def render_monthly_scrap_rate_trend_chart(df, colors=None):
    """
    ★ v3.4 月报用: 月度报废率趋势图 (单折线, 由原"月度合格率趋势图"改造)
    - 纵轴为报废率(%)
    - Y轴范围自动适配: 在数据上下限基础上留出余量, 避免曲线太平看不出变化
    - 悬停显示检测数量/报废数量/报废率完整明细
    返回 Figure
    """
    if colors is None:
        colors = CHART_COLORS
    if df.empty:
        return _empty_fig("月度报废率趋势图")

    df = df.copy()
    df["日期_dt"] = pd.to_datetime(df["日期"], errors="coerce")
    df = df.dropna(subset=["日期_dt"])
    df["月份"] = df["日期_dt"].dt.strftime("%Y-%m")
    monthly = df.groupby("月份").agg(
        检测数量=("检测数量", "sum"),
        报废数量=("报废数量", "sum"),
    ).reset_index()
    monthly["报废率"] = monthly.apply(
        lambda r: round(r["报废数量"] / r["检测数量"] * 100, 2) if r["检测数量"] > 0 else 0.0, axis=1)
    # 取近3个月
    monthly = monthly.sort_values("月份").tail(3)

    if monthly.empty:
        return _empty_fig("月度报废率趋势图")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=monthly["月份"], y=monthly["报废率"],
        mode="lines+markers+text", name="报废率(%)",
        line=dict(color=colors["danger"], width=3),
        marker=dict(size=8),
        text=["{:.1f}%".format(v) for v in monthly["报废率"]],
        textposition="top center",
        connectgaps=True,
        customdata=monthly[["检测数量", "报废数量"]].values.tolist(),
        hovertemplate="<b>%{x}</b>｜检测数量：%{customdata[0]:.0f}件｜报废数量：%{customdata[1]:.0f}件｜报废率：%{y:.2f}%<extra></extra>",
    ))

    # ★ Y轴范围自动适配: 上下留30%余量(不足时固定±1), 曲线起伏清晰可见
    y_min, y_max = float(monthly["报废率"].min()), float(monthly["报废率"].max())
    if y_max > y_min:
        pad = (y_max - y_min) * 0.3
        y_range = [max(0, y_min - pad), y_max + pad]
    else:
        # 各月报废率相同, 给固定余量避免压成一条死线
        y_range = [max(0, y_min - 1), y_max + 1]

    fig.update_layout(
        title="月度报废率趋势图 (近3个月)",
        xaxis_title="月份", yaxis_title="报废率 (%)",
        yaxis=dict(range=y_range),
        height=380, margin=dict(l=40, r=20, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        xaxis=dict(categoryorder="category ascending"),
    )
    return fig
