from . import auth_bp

@auth_bp.route('/')
def index():
    return 'This is the auth blueprint.'
