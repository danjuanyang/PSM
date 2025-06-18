# PSM/app/log/__init__.py
from flask import Blueprint

# 创建一个名为 'log' 的蓝图
# 所有此蓝图下的路由都将以 /log 开头
log_bp = Blueprint('log', __name__, url_prefix='/log')

# 导入路由，将其与蓝图关联
from . import routes
