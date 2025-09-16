#!/bin/sh

# 如果任何命令失败，立即退出。这是铁律。
set -e

echo "--- [Entrypoint] Starting execution ---"

# 1. 应用数据库迁移
echo "--- [Entrypoint] Applying database migrations..."
flask db upgrade
echo "--- [Entrypoint] Database migrations complete."

# 2. 创建超级管理员 (如果不存在)
echo "--- [Entrypoint] Initializing database (superuser)..."
flask init-db
echo "--- [Entrypoint] Database initialization complete."

# 3. 初始化权限和角色
echo "--- [Entrypoint] Seeding initial data (permissions, roles)..."
flask seed
echo "--- [Entrypoint] Initial data seeding complete."

# 4. 初始化系统配置
echo "--- [Entrypoint] Seeding system configs..."
flask seed-configs
echo "--- [Entrypoint] System config seeding complete."

# 所有准备工作完成，用 exec 启动主程序 (gunicorn)
# "$@" 会将 Dockerfile 中 CMD 的指令作为参数传递到这里
echo "--- [Entrypoint] All startup tasks finished. Starting main process... ---"
exec "$@"