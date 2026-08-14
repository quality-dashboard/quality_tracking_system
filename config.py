"""
config.py - 系统配置文件
舍弗勒银川工厂 - 车间质量检测跟踪系统
"""
from typing import List

# ==================== 工序定义 ====================
PROCESSES: List[str] = ["GP12", "锻造", "热处理", "精密磨削"]

# ==================== 班次定义 ====================
SHIFTS: List[str] = ["白班", "夜班"]

# ==================== 预警阈值 ====================
WARNING_THRESHOLD: float = 5.0
CONSECUTIVE_DAYS: int = 3
SCRAP_COUNT_WARNING: int = 5      # ≥5件 → 重点关注（黄色）
SCRAP_COUNT_CRITICAL: int = 10    # ≥10件 → 批次不良（红色，小批次报废）

# ==================== 数据库路径 ====================
DB_PATH: str = "quality_data.db"

# ==================== 系统信息 ====================
SYSTEM_NAME: str = "车间质量检测跟踪系统"
FACTORY_NAME: str = "舍弗勒银川工厂"
VERSION: str = "v3.2.0"