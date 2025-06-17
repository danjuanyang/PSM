import os
from app import create_app, db
from app.models import User, RoleEnum  # 导入一些模型，方便后续操作
from flask_migrate import upgrade

# 从环境变量 'FLASK_CONFIG' 获取配置名，如果没有则使用 'default'
config_name = os.getenv('FLASK_CONFIG') or 'default'

app = create_app(config_name)


@app.cli.command("init-db")
def init_db():
    """自定义CLI命令: 初始化数据库并创建超级管理员."""
    # 应用数据库迁移
    upgrade()

    # 检查是否已存在超级管理员
    if User.query.filter_by(role=RoleEnum.SUPER).first() is None:
        print("正在创建超级用户...")
        super_user = User(
            username='super',
            role=RoleEnum.SUPER,
            full_name='Super Administrator'
        )
        # 设置一个默认密码，实际项目中应更安全地处理
        super_user.set_password('123456')
        db.session.add(super_user)
        db.session.commit()
        print("使用密码“123456”创建的超级用户“superadmin”。")
    else:
        print("超级用户已存在")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=6543, debug=True)

