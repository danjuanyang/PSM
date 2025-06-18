# PSM/app/admin/__init__.py
from flask import Blueprint

# 创建一个名为 'admin' 的蓝图
# 所有此蓝图下的路由都将以 /admin 开头
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# 导入路由，将其与蓝图关联
from . import routes
