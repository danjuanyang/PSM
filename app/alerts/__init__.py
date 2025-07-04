# PSM/app/alerts/__init__.py
from flask import Blueprint

# 统一在这里定义蓝图，并导出
alert_bp = Blueprint('alert', __name__, url_prefix='/alert')

# 导入路由，将路由注册到上面定义的蓝图上
from . import routes
