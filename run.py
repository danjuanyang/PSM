# PSM/run.py
import os
from app import create_app, db
from app.setup import register_commands
from app.models import User, RoleEnum, SystemConfig
from flask_migrate import Migrate, upgrade
import click

# 根据环境变量创建应用实例
config_name = os.getenv('FLASK_CONFIG') or 'default'
app = create_app(config_name)

# 初始化 Flask-Migrate
migrate = Migrate(app, db)

# 注册 'seed' 命令 (来自 app/setup.py)
# 这个命令用来初始化权限等种子数据
register_commands(app)


@app.cli.command('seed-configs')
def seed_configs():
    """
    将config.py和.env中的配置项初始化到system_configs表中。
    """
    # 定义所有可以迁移到数据库的配置项
    CONFIG_KEYS_TO_SEED = {
        # --- General ---
        'APP_NAME': '应用程序的名称',
        'APP_VERSION': '应用程序的版本号',
        'LOG_LEVEL': '应用程序的日志记录级别',
        'ALLOW_REGISTRATION': '是否允许新用户注册',
        'PERMANENT_SESSION_LIFETIME': 'Web会话的生命周期（秒）',
        'POSTS_PER_PAGE': '分页查询时每页显示的项目数',
        'ADMIN_EMAIL': '管理员邮箱地址',
        # --- AI ---
        'AI_MODEL_NAME': '默认使用的AI模型名称',
        'AI_API_KEY': 'AI服务的API密钥',
        'AI_API_BASE_URL': 'AI服务的API基础URL',
        # --- Email ---
        'EMAIL_ENCRYPTION_KEY_FILE': '用于加密邮件密码的密钥文件路径',
        'MAIL_SERVER': '邮件服务器地址 (e.g., smtp.office365.com)',
        'MAIL_PORT': '邮件服务器端口 (e.g., 587)',
        'MAIL_USE_TLS': '邮件服务器是否使用TLS (True/False)',
        'MAIL_USE_SSL': '邮件服务器是否使用SSL (True/False)',
        'MAIL_USERNAME': '邮件发件人用户名 (通常是邮箱地址)',
        'MAIL_PASSWORD': '邮件发件人密码',
        'MAIL_DEFAULT_SENDER': '默认发件人显示名称和地址 (e.g., "Your Name <user@example.com>")',
        # --- Backup ---
        'AUTOBACKUP_CRON_SCHEDULE': '自动备份的Cron表达式 (例如 "0 22 * * *" 表示每天22点)。留空表示禁用。',
    }

    with app.app_context():
        click.echo("开始播种系统配置...")

        for key, description in CONFIG_KEYS_TO_SEED.items():
            # 检查数据库中是否已存在该配置
            existing_config = SystemConfig.query.filter_by(key=key).first()
            if existing_config:
                click.echo(f"'{key}' already exists, skipping.")
                continue

            # 从app.config获取当前值
            value_from_config = app.config.get(key)

            # 特殊处理timedelta和None值
            if key == 'PERMANENT_SESSION_LIFETIME' and hasattr(value_from_config, 'total_seconds'):
                value = str(int(value_from_config.total_seconds()))
            elif value_from_config is None:
                value = '' # 将None转换为空字符串
            else:
                value = str(value_from_config)

            # 只有当值不为空时才添加
            if value:
                new_config = SystemConfig(
                    key=key,
                    value=value,
                    description=description
                )
                db.session.add(new_config)
                click.echo(f"Added '{key}' = '{value}'")

        db.session.commit()
        click.echo("系统配置完成。")


# 注册您自定义的 'init-db' 命令
@app.cli.command("init-db")
def init_db_command():
    """
    自定义CLI命令: 更新数据库并创建超级管理员.
    这是一个集成的初始化命令.
    """
    # 1. 应用所有数据库迁移
    print("正在应用数据库迁移...")
    upgrade()
    print("迁移应用成功!")

    # 2. 检查并创建超级管理员
    if User.query.filter_by(role=RoleEnum.SUPER).first() is None:
        print("创建超管")
        super_user = User(
            username='super',
            email='super@example.com', # 为超级用户添加一个默认邮箱
            role=RoleEnum.SUPER,
        )
        # 设置一个默认密码，实际项目中应更安全地处理
        super_user.set_password('123456')
        db.session.add(super_user)
        db.session.commit()
        print("使用密码 '123456' 创建的超级用户 'super' ")
    else:
        print("超级用户已存在")


# (可选但推荐) 注册 shell 上下文，方便调试
@app.shell_context_processor
def make_shell_context():
    return dict(db=db, User=User, RoleEnum=RoleEnum)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3456)
