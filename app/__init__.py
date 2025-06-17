# API 蓝图根
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_bcrypt import Bcrypt
from flask_cors import CORS

from config import config

# ------------------- 初始化扩展 -------------------
db = SQLAlchemy()
migrate = Migrate()
bcrypt = Bcrypt()


# ------------------- 应用工厂函数 -------------------
def create_app(config_name='default'):
    """
    创建并配置Flask应用实例。
    """
    app = Flask(__name__)

    # 1. 从配置对象加载配置
    app.config.from_object(config[config_name])

    # 2. 初始化扩展
    db.init_app(app)
    migrate.init_app(app, db)
    bcrypt.init_app(app)
    # 允许所有来源的跨域请求，在前后端分离开发中非常方便
    CORS(app, supports_credentials=True)

    # 3. 注册蓝图
    # 从你的蓝图模块中导入蓝图实例
    from .auth import auth_bp
    from .admin import admin_bp
    from .project import project_bp
    from .hr import hr_bp
    from .announcement import announcement_bp
    from .ai import ai_bp
    from .log import log_bp

    # 将蓝图注册到应用上
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(project_bp)
    app.register_blueprint(hr_bp)
    app.register_blueprint(announcement_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(log_bp)

    # Shell 上下文处理器
    @app.shell_context_processor
    def make_shell_context():
        # 方便在 `flask shell` 中直接使用 db 和 models
        from . import models
        return dict(db=db, models=models)

    return app
