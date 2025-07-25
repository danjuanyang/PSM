# PSM/app/activity/__init__.py
from flask import Blueprint

activity_bp = Blueprint('activity', __name__, url_prefix='/activity')

from . import routes
