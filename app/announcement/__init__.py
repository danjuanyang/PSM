from flask import Blueprint

announcement_bp = Blueprint('announcement', __name__, url_prefix='/announcement')

from . import routes
