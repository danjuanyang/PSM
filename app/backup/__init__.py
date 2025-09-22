
# PSM/app/backup/__init__.py
from flask import Blueprint

backup_bp = Blueprint('backup', __name__)

from . import routes
