# PSM/app/files/__init__.py
from flask import Blueprint

# 创建一个名为 'files' 的蓝图
files_bp = Blueprint('files', __name__, url_prefix='/files')

# 导入路由，将其与蓝图关联
# 必须在蓝图创建之后导入，以避免循环依赖
from . import routes
