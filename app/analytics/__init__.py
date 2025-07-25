# PSM/app/analytics/__init__.py
from flask import Blueprint

analytics_bp = Blueprint('analytics', __name__, url_prefix='/analytics')

from . import routes
