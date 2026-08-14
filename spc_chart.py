"""
spc_chart.py - 图表渲染模块
舍弗勒银川工厂 - 车间质量检测跟踪系统
"""
from typing import Optional
from pathlib import Path
import tempfile

import pandas as pd
import plotly.graph_objects as go


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
    """缺陷类型柏拉图"""
    filtered = df.copy()
    if model:
        filtered = filtered[filtered["型号"] == model]

    if filtered.empty or "缺陷类型" not in filtered.columns:
        fig = go.Figure()
        fig.update_layout(
            title="缺陷类型柏拉图",
            annotations=[dict(text="暂无数据", showarrow=False, font=dict(size=16))],
        )
        return fig

    pareto_data = filtered.groupby("缺陷类型")["报废数量"].sum().reset_index()
    pareto_data = pareto_data.sort_values("报废数量", ascending=False).reset_index(drop=True)

    total = pareto_data["报废数量"].sum()
    if total == 0:
        fig = go.Figure()
        fig.update_layout(
            title="缺陷类型柏拉图",
            annotations=[dict(text="报废数量为0", showarrow=False, font=dict(size=16))],
        )
        return fig

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
