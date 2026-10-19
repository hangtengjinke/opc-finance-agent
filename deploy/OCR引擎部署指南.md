# OCR 引擎部署指南（PaddleOCR 本地适配）

> OPC创业财税助手 (c) 2026 瀚钦科技有限公司. All rights reserved.
> 适用版本：平台 v0.4.0+（`opc_fin_agent/paddle_engine.py` 已内置适配器）

## 一、总体原则

1. **本地闭环**：Paddle 推理只跑在你们自己的服务器上，票据图片不出内网，不调任何外部 OCR API。
2. **代码零改动启用**：适配器已实现并与管道联调过接口（`recognize` 返回 文本行+置信度 → 字段锚定 → 置信度门禁）。部署完成后只改 `config/ocr_config.json` 的 `engine` 从 `demo` 改为 `paddle`。
3. **诚实回退**：引擎没装好时系统返回 `engine_not_configured`，绝不编造字段；低置信字段一律进人工核验队列。这两条是产品红线，部署验收必测。

## 二、环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Linux（推荐 Ubuntu 22.04），x86_64 |
| Python | **3.10–3.12**（PaddleOCR 暂不支持 3.13），独立 venv |
| CPU | ≥8 核（`enable_mkldnn` 已开，CPU 推理即可满足日均票据量） |
| GPU | 可选；有卡则 `use_gpu: true` 并改装对应 CUDA 版 paddlepaddle |
| 磁盘 | ≥10GB（paddle + 依赖约 2-3GB，OCR 模型约 20MB，留出余量） |
| 网络 | 仅安装与首次下载模型时需要；日常运行完全离线 |

## 三、安装步骤

```bash
# 1. 进入项目目录，建引擎专用 venv（或并入部署 venv，Python 版本须3.10-3.12）
python3.12 -m venv venv-ocr
source venv-ocr/bin/activate

# 2. 安装（国内走清华镜像）
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r deploy/requirements-ocr.txt

# 3. 首次运行会自动下载识别模型到 ~/.paddleocr（需一次联网）
#    之后断网运行不受影响
python tools/ocr_selftest.py docs/screenshots/03_OCR图片票据接入.png
```

**完全离线的服务器**：在有网机器上按上表装好后，把整个 `venv-ocr` 目录与
`~/.paddleocr` 模型目录一起打包拷贝过去（tar 保权限），或在有网机器预下载模型后
通过 `PaddleOCR(model_dir=...)` 指定本地模型路径（见 `config/ocr_config.json` 的 paddle 节）。

## 四、启用与验收

```bash
# 1. 改配置（config/ocr_config.json）
"engine": "demo"  ->  "engine": "paddle"

# 2. 确认状态探针
curl http://127.0.0.1:8020/api/v1/meta | grep -A3 ocr_engine
#   期望：configured=paddle, paddle_installed=true

# 3. 全量回归（部署服务器上必须全绿）
python tests/smoke_test.py && python tests/wecom_push_test.py \
  && python tests/ocr_test.py && python tests/paddle_adapter_test.py

# 4. 真实票验收（tools/ocr_selftest.py + 拍清晰的数电票截图/纸质票照片各3张）
python tools/ocr_selftest.py /path/to/真实票据照片.png
```

**验收标准（与阶段1评测集口径一致）：**
- 清晰数电票截图：必填字段锚定成功率 ≥90%，锚定字段置信度 ≥0.90 直通规则校验；
- 模糊/残缺票据：必须进 `pending_verification`（人工核验），**严禁给低置信字段定级**；
- 同一张图片重复上传：必须被图片哈希查重拦截；
- 未识别到任何文本：返回"未识别到文本行"提示，不落库、不编造。

## 五、回滚

出任何问题把 `config/ocr_config.json` 的 `engine` 改回 `demo` 并重启服务即可——
演示数据流通道完整可用，业务不中断（真实图片暂时回到"引擎未接入"诚实回退）。

## 六、安全注意

- OCR 引擎部署在**推理节点**，与网关节点同处内网，不开公网端口；
- `~/.paddleocr` 模型目录与本仓库代码一并纳入备份策略；
- 引擎升级（paddlepaddle/paddleocr 小版本以上）必须重跑第4节全部验收项，
  通过后才可在配置中切回 `paddle`。
