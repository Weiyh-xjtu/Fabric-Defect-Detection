"""
Alembic 数据库迁移配置文件

核心作用：
  1. 配置 SQLAlchemy 模型元数据来源（Base.metadata）
  2. 提供在线/离线两种迁移模式
  3. 连接 alembic.ini 中的数据库配置

使用流程：
  1. 修改模型后执行：alembic revision --autogenerate -m "描述"
  2. 执行迁移：alembic upgrade head
  3. 回滚迁移：alembic downgrade -1
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# 将项目根目录加入 Python 路径，确保能导入 app 模块
import sys
from pathlib import Path

# __file__ = alembic/env.py
# __file__.parent = alembic/
# __file__.parent.parent = backend/（项目根目录）
sys.path.insert(0, str(Path(__file__).parent.parent))

# 导入 SQLAlchemy 的 Base 类，用于获取模型元数据
from app.database.session import Base

# 关键：导入所有模型模块，触发模型类的定义和注册
# 原理：当 Python 执行此 import 时，会执行 db_models.py 中的所有类定义
# 模型类继承自 Base，会自动注册到 Base.metadata 中
# 虽然 db_models 变量没被直接使用，但这个导入是必须的
from app.entity import db_models

# 获取 Alembic 配置对象（从 alembic.ini 读取配置）
config = context.config

# 配置 Python 日志系统（读取 alembic.ini 中的 [loggers] 等配置）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 设置迁移目标元数据：告诉 Alembic 从哪里读取模型结构
# Alembic 通过对比 Base.metadata 和数据库当前状态来生成迁移脚本
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    离线迁移模式（不连接数据库）

    使用场景：
      - 需要生成 SQL 脚本文件，手动执行或在其他环境执行
      - 数据库驱动不可用，但需要生成迁移 SQL

    工作方式：
      - 只使用数据库 URL 和元数据生成 SQL 语句
      - 不创建数据库连接，直接输出 SQL 到控制台或文件
    """
    # 从 alembic.ini 读取数据库连接 URL
    url = config.get_main_option("sqlalchemy.url")
    # 配置迁移上下文
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,  # 将参数值直接嵌入 SQL（便于生成独立脚本）
        dialect_opts={"paramstyle": "named"},  # 使用命名参数风格
    )

    # 执行迁移
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    在线迁移模式（连接数据库执行）

    使用场景：
      - 本地开发环境，直接连接数据库执行迁移
      - CI/CD 流程中自动执行迁移

    工作方式：
      - 创建数据库 Engine 和连接
      - 对比数据库当前状态与模型定义
      - 直接在数据库中执行迁移操作
    """
    # 从配置创建数据库连接引擎
    connectable = engine_from_config(
        # 获取配置段（默认是 [alembic]）
        config.get_section(config.config_ini_section, {}),
        # 只读取以 "sqlalchemy." 开头的配置项
        prefix="sqlalchemy.",
        # 使用 NullPool（迁移完成后立即释放连接，避免连接泄漏）
        poolclass=pool.NullPool,
    )

    # 建立数据库连接
    with connectable.connect() as connection:
        # 配置迁移上下文，关联到当前连接
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        # 在事务中执行迁移（确保原子性，失败可回滚）
        with context.begin_transaction():
            context.run_migrations()


# 根据执行模式选择迁移方式
# 通过命令行参数 --offline 或环境变量控制
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
