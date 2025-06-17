from . import hr_bp

@hr_bp.route('/')
def index():
    return 'This is the hr blueprint.'
