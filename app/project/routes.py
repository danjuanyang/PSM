from . import project_bp

@project_bp.route('/')
def index():
    return 'This is the project blueprint.'
