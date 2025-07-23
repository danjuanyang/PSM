from flask import Blueprint

training_bp = Blueprint('training', __name__, url_prefix='/training')

from . import routes