import os
from datetime import timedelta

from dotenv import load_dotenv

# 定位项目根目录
basedir = os.path.abspath(os.path.dirname(__file__))
# 加载 .env 文件中的环境变量
load_dotenv(os.path.join(basedir, '.env'))


class Config:
    """
    基础配置类，包含所有环境通用的配置。
    """
    # 从 .env 文件读取 SECRET_KEY，如果没有则使用一个默认值（强烈建议在.env中设置）
    # SECRET_KEY = os.environ.get('SECRET_KEY') or 'a-hard-to-guess-string'
    SECRET_KEY = os.environ.get('SECRET_KEY')
    # 从环境变量加载会话生命周期，如果没有设置，则默认为1小时
    # 注意：os.environ.get返回的是字符串，需要转换为整数
    lifetime_seconds = int(os.environ.get('PERMANENT_SESSION_LIFETIME', 3600))
    # lifetime_seconds = int(os.environ.get('PERMANENT_SESSION_LIFETIME'))
    PERMANENT_SESSION_LIFETIME = timedelta(seconds=lifetime_seconds)
    ALLOW_REGISTRATION = os.environ.get('ALLOW_REGISTRATION', 'False').lower() in ('true', '1', 't')

    # 数据库配置
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False  # 如果想在控制台看到SQL语句，可以设为 True

    # Celery配置 - 使用数据库作为broker以便于开发
    CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL') or 'sqla+sqlite:///' + os.path.join(basedir, 'celery.db')
    CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND') or 'db+sqlite:///' + os.path.join(basedir, 'celery.db')
    CELERY_TASK_SERIALIZER = 'json'
    CELERY_RESULT_SERIALIZER = 'json'
    CELERY_ACCEPT_CONTENT = ['json']
    CELERY_TIMEZONE = 'UTC'
    CELERY_ENABLE_UTC = True

    UPLOAD_FOLDER = os.path.join(basedir, '..', 'uploads/')
    TEMP_DIR = os.path.join(basedir, '..', 'temp/')

    @staticmethod
    def init_app(app):
        # 确保上传和临时文件夹存在
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        os.makedirs(app.config['TEMP_DIR'], exist_ok=True)


class DevelopmentConfig(Config):
    """
    开发环境配置。
    """
    DEBUG = True
    # 使用 SQLite 数据库，文件将保存在项目根目录下的 data.sqlite
    SQLALCHEMY_DATABASE_URI = os.environ.get('DEV_DATABASE_URL') or \
                              'sqlite:///' + os.path.join(basedir, 'psm-dev.db')
    SQLALCHEMY_ECHO = True  # 开发时建议开启，方便调试


class ProductionConfig(Config):
    """
    生产环境配置。
    """
    DEBUG = False
    # 生产环境中，通常会从环境变量中获取数据库连接（例如 PostgreSQL, MySQL）
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
                              'sqlite:///' + os.path.join(basedir, 'psm.db')  # 默认仍使用sqlite


class TestingConfig(Config):
    """
    测试环境配置。
    """
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('TEST_DATABASE_URL') or \
                              'sqlite:///:memory:'  # 测试时使用内存数据库，速度快


# 将配置类名映射到字符串，方便在 app factory 中根据字符串选择配置
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
