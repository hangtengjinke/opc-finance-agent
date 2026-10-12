# -*- coding: utf-8 -*-
# 瀚钦OPC财税智能体管理平台 (c) 2026 瀚钦科技有限公司. All rights reserved.
"""对象存储门面：原件归档双后端（local 本地目录 / MinIO），零改动切换。

对应《阶段0技术规格》fin-originals 桶规范：
- 原件不可变：同 key 二次写入不覆盖（对象锁语义在应用层先保证）
- MinIO 惰性导入：未安装/未配置时抛出带指引的 StorageNotConfigured，不静默降级
- 引用格式：local 为绝对路径；MinIO 为 "minio://bucket/key"（可反解读取）
"""
import os

from .pg_store import StorageNotConfigured
from .settings import ARCHIVE_DIR, load_storage_config

MINIO_INSTALL_HINT = (
    "MinIO SDK 未安装。生产部署请执行：pip install -r deploy/requirements-prod.txt"
    "（含 minio 包），并按 deploy/PG-MinIO部署指南.md 启动 MinIO 与创建 "
    "fin-originals 桶。"
)


def _load_backend_cfg(cfg=None):
    return (cfg or load_storage_config())["object_store"]


def _save_local(key, data):
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = ARCHIVE_DIR / key
    if not path.exists():  # 原件不可变：已存在则不覆盖
        path.write_bytes(data)
    return str(path)


def _read_local(ref):
    with open(ref, "rb") as f:
        return f.read()


def _get_minio_client(cfg):
    if not cfg.get("endpoint"):
        raise StorageNotConfigured(
            "object_store.backend=minio 但 endpoint 未配置。"
            "请填写 config/storage_config.json 的 endpoint/access_key/secret_key，"
            "部署步骤见 deploy/PG-MinIO部署指南.md。")
    try:
        from minio import Minio
    except ImportError as e:
        raise StorageNotConfigured(MINIO_INSTALL_HINT) from e
    return Minio(
        cfg["endpoint"],
        access_key=cfg.get("access_key") or "",
        secret_key=cfg.get("secret_key") or "",
        secure=bool(cfg.get("secure", False)),
    ), cfg.get("bucket") or "fin-originals"


def _save_minio(key, data, cfg):
    import io
    client, bucket = _get_minio_client(cfg)
    if not client.bucket_exists(bucket):
        raise StorageNotConfigured(
            f"MinIO 桶 {bucket} 不存在。请先创建（fin-originals 为原件不可变桶），"
            "操作命令见 deploy/PG-MinIO部署指南.md。")
    client.put_object(
        bucket, key, io.BytesIO(data), length=len(data),
        content_type="application/octet-stream")
    return f"minio://{bucket}/{key}"


def _read_minio(ref):
    scheme, _, rest = ref.partition("://")
    bucket, _, key = rest.partition("/")
    client, _ = _get_minio_client(_load_backend_cfg())
    resp = client.get_object(bucket, key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


def save_original(key, data, cfg=None):
    """归档原件（不可变）。返回存储引用：local 为路径，MinIO 为 minio://bucket/key。"""
    cfg = _load_backend_cfg(cfg)
    backend = cfg.get("backend", "local")
    if backend == "minio":
        return _save_minio(key, data, cfg)
    return _save_local(key, data)


def read_original(ref):
    """按引用读取原件字节（审计/对账时反向取件）。"""
    if ref.startswith("minio://"):
        return _read_minio(ref)
    return _read_local(ref)


def object_store_status():
    """探针：对象存储后端与 minio SDK 安装状态（供 meta 展示）。"""
    try:
        import minio  # noqa: F401
        sdk = "installed"
    except ImportError:
        sdk = "not_installed"
    cfg = load_storage_config()["object_store"]
    return {
        "backend": cfg.get("backend", "local"),
        "bucket": cfg.get("bucket", "fin-originals"),
        "minio_sdk": sdk,
        "env_dsn_note": "生产建议用 OPC_PG_DSN 环境变量注入DSN",
    }
