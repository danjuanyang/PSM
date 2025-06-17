# PSM/app/auth/__init__.py
from flask import Blueprint

# 创建一个名为'auth'的蓝图
# url_prefix='/auth'意味着这个蓝图下所有的路由都会以/auth开头
auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# 从. import routes 会导入同级目录下的routes.py文件
from . import routes
