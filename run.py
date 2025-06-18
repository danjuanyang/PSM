# PSM/run.py
import os
from app import create_app, db
from app.setup import register_commands
from app.models import User, RoleEnum
from flask_migrate import Migrate, upgrade

# 根据环境变量创建应用实例
config_name = os.getenv('FLASK_CONFIG') or 'default'
app = create_app(config_name)

# 初始化 Flask-Migrate
migrate = Migrate(app, db)

# 注册 'seed' 命令 (来自 app/setup.py)
# 这个命令用来初始化权限等种子数据
register_commands(app)

# 注册您自定义的 'init-db' 命令
@app.cli.command("init-db")
def init_db_command():
    """
    自定义CLI命令: 更新数据库并创建超级管理员.
    这是一个集成的初始化命令.
    """
    # 1. 应用所有数据库迁移
    print("正在应用数据库迁移...")
    upgrade()
    print("迁移应用成功!")

    # 2. 检查并创建超级管理员
    if User.query.filter_by(role=RoleEnum.SUPER).first() is None:
        print("创建超管")
        super_user = User(
            username='super',
            email='super@example.com', # 为超级用户添加一个默认邮箱
            role=RoleEnum.SUPER,
        )
        # 设置一个默认密码，实际项目中应更安全地处理
        super_user.set_password('123456')
        db.session.add(super_user)
        db.session.commit()
        print("使用密码 '123456' 创建的超级用户 'super'。")
    else:
        print("超级用户已存在")


# (可选但推荐) 注册 shell 上下文，方便调试
@app.shell_context_processor
def make_shell_context():
    return dict(db=db, User=User, RoleEnum=RoleEnum)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3456)
