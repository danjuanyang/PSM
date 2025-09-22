# PSM/app/__init__.py
from flask import Flask, g
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_bcrypt import Bcrypt
from flask_cors import CORS
from sqlalchemy import MetaData, exc

from config import config

# ------------------- 辅助函数：从数据库加载配置 -------------------
def load_config_from_db(app):
    with app.app_context():
        try:
            from .models import SystemConfig
            
            # 查询所有配置项
            configs = SystemConfig.query.all()
            
            for config_item in configs:
                key = config_item.key.upper()
                value = config_item.value
                
                # 尝试进行类型转换
                # 1. 布尔值
                if value.lower() in ['true', 'false']:
                    app.config[key] = value.lower() == 'true'
                # 2. 整数
                elif value.isdigit():
                    app.config[key] = int(value)
                # 3. 浮点数
                elif '.' in value and all(part.isdigit() for part in value.split('.', 1)):
                    try:
                        app.config[key] = float(value)
                    except ValueError:
                        app.config[key] = value # 转换失败则保留字符串
                # 4. 字符串
                else:
                    app.config[key] = value
            
            app.logger.info("Successfully loaded configurations from the database.")

        except exc.SQLAlchemyError as e:
            # 在数据库尚未迁移或初始化时，这个错误是正常的，直接忽略
            app.logger.warning(f"Could not load configurations from DB (this is normal during initial setup/migrations): {e}")
        except Exception as e:
            app.logger.error(f"An unexpected error occurred while loading configurations from DB: {e}")


# ------------------- 1. 初始化扩展 -------------------
# 将所有扩展实例在全局范围内创建
# --- 2. 定义一个命名约定 ---
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"
}
# --- 3. 在初始化db时应用这个约定 ---
#     创建一个包含命名约定的 MetaData 对象
metadata = MetaData(naming_convention=convention)
#     将 metadata 传递给 SQLAlchemy 的构造函数
db = SQLAlchemy(metadata=metadata)

migrate = Migrate()
bcrypt = Bcrypt()
login_manager = LoginManager()

# 当未登录用户访问需要登录的视图时，重定向到的端点。
# 'auth.login' 指向 auth_bp 蓝图下的 login 视图函数
login_manager.login_view = 'auth.login'
# 为前后端分离API设置，如果未认证，不重定向而是返回401错误
# 您可以在这里添加一个自定义的unauthorized_handler
# login_manager.unauthorized_handler = lambda: ('Unauthorized', 401)


# ------------------- 2. 应用工厂函数 -------------------
def create_app(config_name='default'):
    """
    创建并配置Flask应用实例。
    这是一个标准的工厂模式，用于创建应用。
    """
    app = Flask(__name__)

    # a. 从配置对象加载配置
    app.config.from_object(config[config_name])
    
    # a.1 初始化配置（例如创建文件夹）
    config[config_name].init_app(app)

    # b. 使用app实例初始化扩展
    # 这一步将扩展与Flask应用关联起来
    db.init_app(app)
    
    # 新增：从数据库加载并覆盖配置
    load_config_from_db(app)

    # 在 init_app 之前，将 render_as_batch=True 设置给 Migrate
    # 这确保 Alembic 在 SQLite 上总是使用批处理模式
    migrate.init_app(app, db, render_as_batch=True)

    bcrypt.init_app(app)
    login_manager.init_app(app)
    CORS(app, supports_credentials=True) # 允许跨域请求，并支持credentials（如cookies）

    @app.before_request
    def before_request():
        g.app = app

    # c. 设置 user_loader 回调函数
    # 这个函数告诉Flask-Login如何通过ID加载用户
    # 必须在 login_manager.init_app(app) 之后定义
    from .models import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # d. 注册蓝图
    # 将应用的不同模块组织起来
    from .auth import auth_bp
    from .admin import admin_bp
    from .project import project_bp
    from .hr import hr_bp
    from .announcement import announcement_bp
    from .ai import ai_bp
    from .log import log_bp
    from .alerts import alert_bp
    from .files import files_bp
    from .utils import utils_bp
    from .training import training_bp
    from .activity import activity_bp
    from .analytics import analytics_bp
    from .email import email_bp
    from .knowledge_base import kb_bp
    from .backup import backup_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(project_bp)
    app.register_blueprint(hr_bp)
    app.register_blueprint(announcement_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(log_bp)
    app.register_blueprint(alert_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(utils_bp)
    app.register_blueprint(training_bp)
    app.register_blueprint(activity_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(email_bp, url_prefix='/email')
    app.register_blueprint(kb_bp, url_prefix='/kb')
    app.register_blueprint(backup_bp)
    
    # e. 启动临时文件清理调度器
    from .files.cleanup_scheduler import cleanup_scheduler
    cleanup_scheduler.init_app(app)
    
    # 在应用上下文中启动清理调度器
    with app.app_context():
        cleanup_scheduler.start_cleanup_scheduler()
    
    # f. 启动邮件任务调度器
    from .email.scheduler import email_scheduler
    with app.app_context():
        email_scheduler.init_tasks()

    # g. 启动备份任务调度器
    from .backup.scheduler import backup_scheduler

    # f. Shell 上下文处理器 (可选，但推荐)
    @app.shell_context_processor
    def make_shell_context():
        # 方便在 `flask shell` 中直接使用 db 和 models，便于调试
        from . import models
        return dict(db=db, models=models)

    # g. 返回创建好的应用实例
    return app
