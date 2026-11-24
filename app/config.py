"""应用配置：从项目根目录的 .env 读取，不存在则使用默认值 / 环境变量。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # python-dotenv 未安装时直接读环境变量
    pass

# ===== AI =====
# DeepSeek 开放平台申请：https://platform.deepseek.com
API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
# deepseek-flash 已支持图片输入；也可配置为 deepseek-v4-flash-vision-exp 等旧名
AI_MODEL = os.getenv("AI_MODEL", "deepseek-flash").strip()
# OpenAI 兼容接口地址，可换成其他厂商（如智谱 / 通义）的兼容端点
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.deepseek.com/v1").strip()
# 开发 / 演示模式：不调用真实 API，返回模拟批改结果
AI_MOCK = os.getenv("AI_MOCK", "0").strip().lower() in {"1", "true", "yes"}

# 一次 AI 请求最多携带的图片张数（题目裁剪 + 答案页），超出会自动分成多次顺序请求
MAX_IMAGES_PER_REQUEST = max(4, int(os.getenv("MAX_IMAGES_PER_REQUEST", "18")))

# ===== 服务 =====
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))

# ===== 存储 =====
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
TMP_DIR = DATA_DIR / "tmp"
HISTORY_DIR = DATA_DIR / "history"
