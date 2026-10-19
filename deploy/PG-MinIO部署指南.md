# PG/MinIO 生产化部署指南

> OPC创业财税助手 V1.0 ｜ 存储后端切换手册
> 对应《阶段0技术规格-接口边界与数据分区设计》数据分区章节。
> 适用：开发机（SQLite/本地目录）→ 生产服务器（PostgreSQL/MinIO）切换。

## 0. 总原则

- **代码零改动切换**：后端由 `config/storage_config.json` 的 `backend` 字段决定，
  业务代码通过兼容层（`pg_store.PgCompatConn` / `objectstore` 门面）访问，切换只改配置。
- **诚实回退**：依赖未安装或连接失败时系统报带指引的明确错误（`StorageNotConfigured`），
  绝不静默降级到错误后端。
- **回滚兜底**：迁移不删除 SQLite 原库；切换出问题把 `backend` 改回 `sqlite` 即回滚。

## 1. PostgreSQL 安装与建库

Ubuntu/Debian 示例（也可用官方 Docker 镜像 postgres:16）：

```bash
sudo apt install postgresql-16
sudo -u postgres psql <<'SQL'
CREATE USER opc_app WITH PASSWORD '强密码';
CREATE DATABASE opc_finance OWNER opc_app;
SQL
```

建议（上线检查项，非本指南强制）：数据库启用 `log_statement='ddl'`，
审计表 `audit_events` 的写权限仅给应用账号，另建只读账号供审计核查。

## 2. MinIO 安装与建桶

```bash
# 下载 minio server 与 mc 客户端（离线环境提前下载好二进制）
./minio server /data/minio --console-address ":9001"
mc alias set local http://127.0.0.1:9000 <ACCESS_KEY> <SECRET_KEY>
mc mb local/fin-originals          # 原件桶（不可变）
# 可选加固：开启对象锁（版本化桶）实现服务端不可变
mc version enable local/fin-originals
```

`fin-originals` 桶存放票据原件（JSON/图片），同 key 不覆盖（应用层已保证，
开启版本锁后服务端再保一道）。票据原件不进向量库、不进任何对外通道。

## 3. 安装生产依赖

```bash
pip install -r requirements.txt -r deploy/requirements-prod.txt
```

## 4. 配置切换

编辑 `config/storage_config.json`：

```json
{
  "database": {"backend": "pg", "pg_dsn": ""},
  "object_store": {
    "backend": "minio",
    "endpoint": "127.0.0.1:9000",
    "access_key": "..." , "secret_key": "...",
    "bucket": "fin-originals", "secure": false
  }
}
```

**DSN 安全建议**：`pg_dsn` 留空，改用环境变量注入（避免敏感信息落盘）：

```bash
export OPC_PG_DSN="postgresql://opc_app:强密码@127.0.0.1:5432/opc_finance"
```

secret_key 同理可改为部署时写入配置并 `chmod 600`，或接入密钥管理。

## 5. 存量数据迁移（有历史 SQLite 数据时）

```bash
python tools/migrate_sqlite_to_pg.py
```

迁移工具自带验收：行数核对 + **审计哈希链完整性校验**（重算每条 event_hash），
任一失败即退出。迁移完成后 SQLite 原库保留作回滚兜底。

## 6. 部署验收（顺序执行，全部通过才算完成）

1. `python tools/migrate_sqlite_to_pg.py --sqlite data/opc.db`（如有存量）→ 行数一致、审计链通过
2. 启动服务后访问 `GET /api/v1/meta` →
   `storage.backend == "pg"`、`pg_driver == "installed"`、
   `object_store.backend == "minio"`、`minio_sdk == "installed"`
3. 全量回归：六套测试全部通过（见 README）
4. 走一笔真实上传 → 归档引用应为 `minio://fin-originals/<hash>...`，
   `mc ls local/fin-originals` 能看到对象
5. 待办推送一次 → `pushed_cards` 表有新行（PG 端 `\dt` 核对）

## 7. 回滚

`backend` 改回 `sqlite` / `local`，重启即回滚。MinIO 中的原件与本地目录
内容一致（同一批迁移产物），无需数据回迁。

## 8. 备份与恢复（对接运维）

- **PG**：`pg_dump -Fc opc_finance > opc_finance_$(date +%F).dump`，
  每日定时 + 异机离线副本；恢复 `pg_restore -d opc_finance dump文件`。
- **MinIO**：`mc mirror local/fin-originals /备份盘/fin-originals`，
  每日增量；恢复 `mc mirror /备份盘/fin-originals local/fin-originals`。
- 恢复演练：每季度抽一次，从备份机恢复到测试库跑审计链校验。

## 9. 常见问题

| 现象 | 处置 |
|---|---|
| meta 显示 `pg_driver: not_installed` | 依赖未装：执行第 3 步 |
| 启动报 `PostgreSQL 连接失败` | 核对 DSN / pg_hba.conf 是否放行内网地址 |
| 报 `MinIO 桶 fin-originals 不存在` | 执行第 2 步 `mc mb` |
| INSERT 报主键冲突 | 迁移后 identity 序列未修复 → 重跑第 5 步 |
| 想临时回退演示模式 | backend 全改回 sqlite/local，重启 |
