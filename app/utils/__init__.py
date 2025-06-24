# PSM/app/utils/__init__.py
from flask import Blueprint

# 创建一个名为 'admin' 的蓝图
# 所有此蓝图下的路由都将以 /admin 开头
utils_bp = Blueprint('utils', __name__, url_prefix='/utils')

# 导入路由，将其与蓝图关联
from . import routes
