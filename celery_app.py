# PSM/celery_app.py
from celery import Celery

# 1. 创建一个未配置的Celery实例
# 我们不再在这里创建Flask应用
celery = Celery(__name__)

def make_celery(app):
    """
    用Flask应用的配置来配置Celery实例。
    这个函数将由 app/__init__.py 中的 create_app 调用。
    """
    celery.conf.update(
        broker_url=app.config['CELERY_BROKER_URL'],
        result_backend=app.config['CELERY_RESULT_BACKEND'],
        task_serializer=app.config.get('CELERY_TASK_SERIALIZER', 'json'),
        result_serializer=app.config.get('CELERY_RESULT_SERIALIZER', 'json'),
        accept_content=app.config.get('CELERY_ACCEPT_CONTENT', ['json']),
        timezone=app.config.get('CELERY_TIMEZONE', 'UTC'),
        enable_utc=app.config.get('CELERY_ENABLE_UTC', True),
        task_track_started=True,
        task_routes={
            'app.files.merge_tasks.*': {'queue': 'file_merge'},
        },
        worker_prefetch_multiplier=1,
        task_acks_late=True,
    )
    
    class ContextTask(celery.Task):
        """确保任务在Flask应用上下文中运行"""
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)
    
    celery.Task = ContextTask
    return celery
