# PSM/app/alerts/__init__.py
from flask import Blueprint

# 创建一个名为 'alerts' 的蓝图
# 所有此蓝图下的路由都将以 /alerts 开头
alerts_bp = Blueprint('alerts', __name__, url_prefix='/alerts')

# 导入路由，将其与蓝图关联
from . import routes
