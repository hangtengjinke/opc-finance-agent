# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""全局配置与企业主数据加载（Demo占位，软著纪律D3）。"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = BASE_DIR / "config" / "demo_master_data.json"
DATA_DIR = BASE_DIR / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
WEB_DIR = BASE_DIR / "web"

APP_NAME = "瀚钦OPC财税智能体管理平台"
APP_VERSION = "0.5.0"


def load_master_data():
    """加载企业主数据。主数据为结构化配置，由规则引擎调用，不进入模型上下文。"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)
