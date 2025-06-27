# PSM/app/hr/__init__.py
from flask import Blueprint

# 创建一个名为 'hr' 的蓝图
# 所有此蓝图下的路由都将以 /hr 开头
hr_bp = Blueprint('hr', __name__, url_prefix='/hr')

# 导入路由，将其与蓝图关联
from . import routes
