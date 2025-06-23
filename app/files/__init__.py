# PSM/app/files/__init__.py
from flask import Blueprint

# 创建一个名为 'files' 的蓝图
# 所有此蓝图下的路由都将以 /file 开头
files_bp = Blueprint('files', __name__, url_prefix='/files')

# 导入路由，将其与蓝图关联
from . import routes
