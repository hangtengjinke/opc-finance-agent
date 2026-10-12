# 瀚钦OPC财税智能体管理平台

> (c) 2026 瀚钦科技有限公司. All rights reserved.
> V0.2.0 开发预览（MVP 第二增量）｜软件著作权申报名：瀚钦OPC财税智能体管理平台V1.0（冻结后申报）

本地部署的企业财税智能体 MVP 脚手架，面向小白财务人员，实现《总体建设方案》首期主流程的垂直切片。

## 职责边界（与总体方案一致）

- **L0/L1（本平台做）**：票据归集、字段抽取、查重、规则校验、处理建议（A/B/C分级）、归档留痕、征期日历提醒、带出处问答
- **L2（只做人做）**：付款、申报、缴税、记账、开票——平台不提供任何自动化接口，不代填任何登录凭证
- **红线**：不模拟登录网银/电子税务局、不持有密码/U盾/验证码、不自动对外提交

## 快速开始

```bash
pip install -r requirements.txt
python -m uvicorn opc_fin_agent.main:app --port 8020
# 浏览器打开 http://127.0.0.1:8020
python tests/smoke_test.py       # 主流程冒烟测试（5用例）
python tests/wecom_push_test.py  # 待办推送冒烟测试（6用例）
```

## 目录结构

```
opc-finance-agent/
├── config/demo_master_data.json   # 企业主数据（Demo占位，D3纪律）
├── opc_fin_agent/
│   ├── main.py        # FastAPI网关（对外唯一入口）
│   ├── pipeline.py    # 主流程：安检→抽取→查重→规则→建议→归档
│   ├── rules.py       # 规则引擎（确定性代码，R01-R06）
│   ├── db.py          # SQLite存储 + 哈希链审计
│   ├── calendar_seed.py # 征期日历种子数据
│   ├── wecom.py       # 企业微信待办卡片推送（字段白名单+脱敏+dry-run+去重）
│   ├── kb.py          # 合规问答（无来源拒答）
│   └── settings.py    # 配置与主数据加载
├── config/wecom_config.json # 推送配置（webhook留空即dry-run，D3纪律）
├── web/index.html     # 单页操作界面（截图库素材来源）
├── tests/smoke_test.py # 主流程冒烟测试
├── tests/wecom_push_test.py # 推送通道冒烟测试
└── docs/screenshots/  # 操作手册截图库（D4纪律）
```

## 开发纪律（软著申报规划书 D1-D8）

- D1 版权头：所有源文件头部已带版权声明
- D2 版本：git 管理，MVP完成打 v1.0 标签冻结
- D3 脱敏：配置与代码仅使用 Demo 占位符，禁止真实凭证入库
- D4 截图：功能完成即截真实界面存 docs/screenshots
- D7 注释：源码内不放大段中文说明文字（说明性内容放操作手册）
- 拟拆子项（票据识别校验引擎、待办提醒网关）须保持可独立运行（D6）

## 生产化路线（本骨架刻意留白的部分）

- OCR 接入与置信度分级（当前仅支持结构化数电票JSON）
- PostgreSQL 分区化存储 + MinIO 对象存储（当前为SQLite/本地目录）
- 本地大模型推理与RAG检索（当前为演示知识库）
- 企业微信真实webhook启用（当前dry-run模式已验证消息体格式，启用仅需在config/wecom_config.json填入群机器人webhook并纳入外联清单管理）
- 等保定级、单点登录、字段级加密遮蔽

以上替换点均按《阶段0技术规格》的接口编号（IF-01~07）预留，禁止在骨架上直接绕过边界实现。
