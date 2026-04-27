"""
生成 Grafana 风格 AI Service 监控仪表板截图（演示数据）
用于简历/演示展示监控体系建设成果
"""

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from datetime import datetime, timedelta

# ============================================================
# Grafana 深色主题配色
# ============================================================
BG_COLOR = '#111217'
PANEL_BG = '#181b1f'
PANEL_BORDER = '#2c3235'
TEXT_COLOR = '#c7d0d9'
TITLE_COLOR = '#f0f0f0'
GRID_COLOR = '#2c3235'
ACCENT_GREEN = '#73bf69'
ACCENT_BLUE = '#5794f2'
ACCENT_ORANGE = '#f2c94c'
ACCENT_RED = '#e02f44'
ACCENT_PURPLE = '#b877d9'

plt.rcParams['font.family'] = ['Microsoft YaHei', 'Noto Sans SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

np.random.seed(42)

# ============================================================
# 生成模拟时序数据
# ============================================================
def generate_series(days=7, base=10, noise=2, trend=0):
    """生成模拟时序数据"""
    n_points = days * 24  # 每小时一个点
    x = np.arange(n_points)
    # 基础值 + 趋势 + 日周期波动 + 随机噪声
    daily_cycle = 3 * np.sin(2 * np.pi * x / 24)
    weekly_pattern = np.random.normal(0, noise, n_points)
    y = base + trend * x / n_points + daily_cycle + weekly_pattern
    y = np.maximum(y, 0.1)
    return x, y

# 时间标签
times = [datetime.now() - timedelta(hours=24*7 - i) for i in range(7*24)]
time_labels = [t.strftime('%m-%d %H:%M') for t in times]

# 各指标数据
x_qps, y_qps = generate_series(days=7, base=15, noise=3, trend=5)
x_lat, y_lat = generate_series(days=7, base=8, noise=1.5, trend=-2)
x_tool, y_tool = generate_series(days=7, base=2, noise=0.5)
x_llm, y_llm = generate_series(days=7, base=12, noise=2)

# ============================================================
# 创建画布
# ============================================================
fig = plt.figure(figsize=(19.2, 10.8), facecolor=BG_COLOR)
fig.patch.set_facecolor(BG_COLOR)

# 顶部导航栏
ax_nav = fig.add_axes([0, 0.96, 1, 0.04])
ax_nav.set_facecolor(BG_COLOR)
ax_nav.set_xlim(0, 1)
ax_nav.set_ylim(0, 1)
ax_nav.axis('off')

# Grafana logo + 面包屑
ax_nav.text(0.015, 0.5, '☰', fontsize=14, color=TEXT_COLOR, va='center', ha='left')
ax_nav.text(0.04, 0.5, 'Home   >   Dashboards   >   AI Service Overview', fontsize=10, color='#8e8e8e', va='center', ha='left')

# 顶部右侧按钮区
ax_nav.text(0.88, 0.5, '+ Add  ▼', fontsize=10, color='#5794f2', va='center', ha='center')
ax_nav.text(0.92, 0.5, '⚙', fontsize=12, color=TEXT_COLOR, va='center', ha='center')
ax_nav.text(0.95, 0.5, '[ ] Last 7 days  ▼', fontsize=10, color=TEXT_COLOR, va='center', ha='center')
ax_nav.text(0.985, 0.5, '▲', fontsize=10, color=TEXT_COLOR, va='center', ha='center')

# ============================================================
# GridSpec 布局
# ============================================================
gs = GridSpec(4, 4, figure=fig,
              left=0.01, right=0.99, top=0.95, bottom=0.01,
              wspace=0.01, hspace=0.01)

def draw_panel_bg(ax, title):
    """绘制面板背景和标题"""
    ax.set_facecolor(PANEL_BG)
    for spine in ax.spines.values():
        spine.set_color(PANEL_BORDER)
        spine.set_linewidth(0.5)
    ax.tick_params(colors=TEXT_COLOR, labelsize=8)
    ax.set_title(title, fontsize=11, color=TITLE_COLOR, fontweight='bold',
                 loc='left', pad=8, backgroundcolor=PANEL_BG)
    ax.grid(True, color=GRID_COLOR, linewidth=0.3, alpha=0.5)
    ax.set_axisbelow(True)

def draw_empty_panel(ax, title):
    """绘制无数据面板"""
    ax.set_facecolor(PANEL_BG)
    for spine in ax.spines.values():
        spine.set_color(PANEL_BORDER)
        spine.set_linewidth(0.5)
    ax.text(0.5, 0.5, 'No data', fontsize=24, color='#2c3235',
            ha='center', va='center', transform=ax.transAxes)
    ax.set_title(title, fontsize=11, color=TITLE_COLOR, fontweight='bold',
                 loc='left', pad=8, backgroundcolor=PANEL_BG)
    ax.set_xticks([])
    ax.set_yticks([])

# ---- Row 1: 请求 QPS (左) + 请求延迟 P95 (右) ----
# 请求 QPS - 线图
ax1 = fig.add_subplot(gs[0, :2])
draw_panel_bg(ax1, '▲  请求 QPS')
ax1.plot(x_qps, y_qps, color=ACCENT_GREEN, linewidth=1.5, alpha=0.9)
ax1.fill_between(x_qps, y_qps, alpha=0.15, color=ACCENT_GREEN)
ax1.set_ylabel('requests/sec', fontsize=8, color='#8e8e8e')
xticks_idx = list(range(0, len(x_qps), 24))
ax1.set_xticks(xticks_idx)
ax1.set_xticklabels([time_labels[i][:5] for i in xticks_idx], fontsize=7, color='#8e8e8e')
ax1.set_ylim(0, max(y_qps) * 1.2)
# 添加当前值标注
ax1.text(0.98, 0.95, f'{y_qps[-1]:.1f}', fontsize=20, color=ACCENT_GREEN,
         ha='right', va='top', transform=ax1.transAxes, fontweight='bold')

# 请求延迟 P95 - 线图
ax2 = fig.add_subplot(gs[0, 2:])
draw_panel_bg(ax2, '▲  请求延迟 P95')
ax2.plot(x_lat, y_lat, color=ACCENT_ORANGE, linewidth=1.5, alpha=0.9)
ax2.fill_between(x_lat, y_lat, alpha=0.15, color=ACCENT_ORANGE)
ax2.set_ylabel('seconds', fontsize=8, color='#8e8e8e')
xticks_idx = list(range(0, len(x_lat), 24))
ax2.set_xticks(xticks_idx)
ax2.set_xticklabels([time_labels[i][:5] for i in xticks_idx], fontsize=7, color='#8e8e8e')
ax2.set_ylim(0, max(y_lat) * 1.3)
ax2.text(0.98, 0.95, f'{y_lat[-1]:.1f}s', fontsize=20, color=ACCENT_ORANGE,
         ha='right', va='top', transform=ax2.transAxes, fontweight='bold')

# ---- Row 2: 错误率 + 活跃请求数 + 工具调用分布 ----
# 错误率 - 单值 + 小趋势
ax3 = fig.add_subplot(gs[1, 0])
draw_panel_bg(ax3, '▲  错误率')
err_data = np.random.uniform(0.5, 3.0, 7*24)
ax3.plot(err_data, color=ACCENT_RED, linewidth=1)
ax3.set_xticks([])
ax3.set_yticks([])
ax3.text(0.5, 0.5, f'{np.mean(err_data[-24:]):.2f}%', fontsize=28, color=ACCENT_RED,
         ha='center', va='center', transform=ax3.transAxes, fontweight='bold')
ax3.text(0.5, 0.2, f'avg {np.mean(err_data):.2f}%', fontsize=10, color='#8e8e8e',
         ha='center', va='center', transform=ax3.transAxes)

# 活跃请求数 - 单值
ax4 = fig.add_subplot(gs[1, 1])
draw_panel_bg(ax4, '▲  活跃请求数')
ax4.set_xticks([])
ax4.set_yticks([])
ax4.text(0.5, 0.5, '3', fontsize=36, color=ACCENT_BLUE,
         ha='center', va='center', transform=ax4.transAxes, fontweight='bold')

# 工具调用分布 - 饼图
ax5 = fig.add_subplot(gs[1, 2:])
draw_panel_bg(ax5, '▲  工具调用分布')
tool_names = ['query_timesheet', 'compute_statistics', 'sql_query', 'save_workhour', 'generate_report']
tool_vals = [45, 25, 15, 10, 5]
colors_pie = [ACCENT_GREEN, ACCENT_BLUE, ACCENT_ORANGE, ACCENT_PURPLE, ACCENT_RED]
explode = (0.02, 0.02, 0.02, 0.02, 0.02)
wedges, texts, autotexts = ax5.pie(tool_vals, labels=tool_names, autopct='%1.0f%%',
                                     colors=colors_pie, explode=explode,
                                     textprops={'fontsize': 8, 'color': TEXT_COLOR},
                                     pctdistance=0.75, labeldistance=1.1)
for autotext in autotexts:
    autotext.set_fontsize(7)
    autotext.set_color('#111217')
ax5.set_facecolor(PANEL_BG)

# ---- Row 3: 工具调用延迟 P95 + LLM 调用延迟 P95 ----
ax6 = fig.add_subplot(gs[2, :2])
draw_panel_bg(ax6, '▲  工具调用延迟 P95')
ax6.plot(x_tool, y_tool, color=ACCENT_PURPLE, linewidth=1.5)
ax6.fill_between(x_tool, y_tool, alpha=0.15, color=ACCENT_PURPLE)
ax6.set_ylabel('seconds', fontsize=8, color='#8e8e8e')
xticks_idx = list(range(0, len(x_tool), 24))
ax6.set_xticks(xticks_idx)
ax6.set_xticklabels([time_labels[i][:5] for i in xticks_idx], fontsize=7, color='#8e8e8e')
ax6.set_ylim(0, max(y_tool) * 1.5)
ax6.text(0.98, 0.95, f'{y_tool[-1]:.2f}s', fontsize=18, color=ACCENT_PURPLE,
         ha='right', va='top', transform=ax6.transAxes, fontweight='bold')

ax7 = fig.add_subplot(gs[2, 2:])
draw_panel_bg(ax7, '▲  LLM 调用延迟 P95')
ax7.plot(x_llm, y_llm, color=ACCENT_BLUE, linewidth=1.5)
ax7.fill_between(x_llm, y_llm, alpha=0.15, color=ACCENT_BLUE)
ax7.set_ylabel('seconds', fontsize=8, color='#8e8e8e')
xticks_idx = list(range(0, len(x_llm), 24))
ax7.set_xticks(xticks_idx)
ax7.set_xticklabels([time_labels[i][:5] for i in xticks_idx], fontsize=7, color='#8e8e8e')
ax7.set_ylim(0, max(y_llm) * 1.3)
ax7.text(0.98, 0.95, f'{y_llm[-1]:.1f}s', fontsize=18, color=ACCENT_BLUE,
         ha='right', va='top', transform=ax7.transAxes, fontweight='bold')

# ---- Row 4: 意图分布 (柱状图) ----
ax8 = fig.add_subplot(gs[3, :2])
draw_panel_bg(ax8, '▲  意图分布')
intents = ['工时查询', '统计分析', 'SQL查询', '周报生成', '工时填报', '知识问答']
intent_vals = [35, 22, 12, 8, 15, 8]
bars = ax8.bar(intents, intent_vals, color=[ACCENT_GREEN, ACCENT_BLUE, ACCENT_ORANGE,
                                             ACCENT_PURPLE, ACCENT_RED, '#33b5e5'],
               width=0.6, edgecolor=PANEL_BORDER, linewidth=0.5)
ax8.set_ylabel('count', fontsize=8, color='#8e8e8e')
ax8.tick_params(axis='x', labelsize=8)
for bar, val in zip(bars, intent_vals):
    ax8.text(bar.get_x() + bar.get_width()/2., bar.get_height() + 0.5,
             str(val), ha='center', va='bottom', fontsize=8, color=TEXT_COLOR)
ax8.set_ylim(0, max(intent_vals) * 1.2)

# 右下角留白或放 RAG 面板
ax9 = fig.add_subplot(gs[3, 2:])
draw_panel_bg(ax9, '▲  RAG 查询成功率')
rag_data = np.random.uniform(92, 98, 7*24)
ax9.plot(rag_data, color=ACCENT_GREEN, linewidth=1.5)
ax9.fill_between(range(len(rag_data)), rag_data, 90, alpha=0.15, color=ACCENT_GREEN)
ax9.axhline(y=95, color=ACCENT_ORANGE, linestyle='--', linewidth=0.8, alpha=0.7, label='target 95%')
ax9.set_ylabel('percent', fontsize=8, color='#8e8e8e')
ax9.set_ylim(88, 100)
xticks_idx = list(range(0, len(rag_data), 24))
ax9.set_xticks(xticks_idx)
ax9.set_xticklabels([time_labels[i][:5] for i in xticks_idx], fontsize=7, color='#8e8e8e')
ax9.text(0.98, 0.95, f'{rag_data[-1]:.1f}%', fontsize=18, color=ACCENT_GREEN,
         ha='right', va='top', transform=ax9.transAxes, fontweight='bold')
ax9.legend(loc='upper left', fontsize=7, facecolor=PANEL_BG, edgecolor=PANEL_BORDER, labelcolor=TEXT_COLOR)

# ============================================================
# 保存截图
# ============================================================
output_path = 'E:/huan/工时管理系统/trunk/1 源代码/1.0 系统代码/ai-service/docs/benchmarks/screenshots/grafana-ai-service-mock.png'
plt.savefig(output_path, dpi=150, facecolor=BG_COLOR, edgecolor='none', bbox_inches='tight', pad_inches=0)
plt.close()

print(f'已生成 Grafana 模拟仪表板截图: {output_path}')
