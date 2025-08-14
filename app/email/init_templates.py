# PSM/app/email/init_templates.py
"""
邮件模板初始化脚本
用于创建默认的邮件模板
"""
from datetime import datetime
from .. import db
from ..models import EmailTemplate, EmailTemplateTypeEnum


def init_email_templates():
    """初始化默认邮件模板"""
    
    # 周报模板
    weekly_report_template = EmailTemplate(
        name="项目周报",
        template_type=EmailTemplateTypeEnum.WEEKLY_REPORT,
        subject="【项目周报】{{ week_start }} - {{ week_end }}",
        body_html="""
<!DOCTYPE html>
<html>
<head>
    <style>
        body { font-family: Arial, sans-serif; }
        .container { max-width: 800px; margin: 0 auto; padding: 20px; }
        .header { background-color: #4CAF50; color: white; padding: 15px; text-align: center; }
        .content { padding: 20px; background-color: #f9f9f9; }
        .project-item { margin: 10px 0; padding: 10px; background: white; border-left: 3px solid #4CAF50; }
        .footer { margin-top: 20px; padding: 10px; text-align: center; color: #666; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #4CAF50; color: white; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>项目周报</h2>
            <p>{{ week_start }} 至 {{ week_end }}</p>
        </div>
        <div class="content">
            <h3>本周项目进展</h3>
            <p>本周共有 <strong>{{ total_projects }}</strong> 个项目有更新。</p>
            
            <table>
                <thead>
                    <tr>
                        <th>项目名称</th>
                        <th>负责人</th>
                        <th>进度</th>
                        <th>状态</th>
                    </tr>
                </thead>
                <tbody>
                    {% for project in projects %}
                    <tr>
                        <td>{{ project.name }}</td>
                        <td>{{ project.employee }}</td>
                        <td>{{ project.progress }}%</td>
                        <td>{{ project.status }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        <div class="footer">
            <p>此邮件由PSM系统自动发送</p>
            <p>{{ current_date }} {{ current_time }}</p>
        </div>
    </div>
</body>
</html>
""",
        body_text="""
项目周报 ({{ week_start }} - {{ week_end }})
==========================================

本周项目进展
-----------
本周共有 {{ total_projects }} 个项目有更新。

项目列表：
{% for project in projects %}
- {{ project.name }}
  负责人：{{ project.employee }}
  进度：{{ project.progress }}%
  状态：{{ project.status }}
{% endfor %}

------------------------------------------
此邮件由PSM系统自动发送
{{ current_date }} {{ current_time }}
""",
        variables={
            "week_start": "周开始日期",
            "week_end": "周结束日期",
            "total_projects": "项目总数",
            "projects": "项目列表（包含name, employee, progress, status）",
            "current_date": "当前日期",
            "current_time": "当前时间"
        },
        description="每周项目进展汇总报告"
    )
    
    # 月报模板
    monthly_report_template = EmailTemplate(
        name="项目月报",
        template_type=EmailTemplateTypeEnum.MONTHLY_REPORT,
        subject="【项目月报】{{ month }} 月度总结",
        body_html="""
<!DOCTYPE html>
<html>
<head>
    <style>
        body { font-family: Arial, sans-serif; }
        .container { max-width: 800px; margin: 0 auto; padding: 20px; }
        .header { background-color: #2196F3; color: white; padding: 15px; text-align: center; }
        .content { padding: 20px; background-color: #f9f9f9; }
        .stat-box { display: inline-block; margin: 10px; padding: 15px; background: white; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .stat-number { font-size: 24px; font-weight: bold; color: #2196F3; }
        .footer { margin-top: 20px; padding: 10px; text-align: center; color: #666; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>{{ month }} 月度项目总结</h2>
        </div>
        <div class="content">
            <h3>本月数据统计</h3>
            <div class="stat-box">
                <div class="stat-number">{{ total_projects }}</div>
                <div>项目总数</div>
            </div>
            <div class="stat-box">
                <div class="stat-number">{{ completed_projects }}</div>
                <div>已完成项目</div>
            </div>
            <div class="stat-box">
                <div class="stat-number">{{ completion_rate }}%</div>
                <div>完成率</div>
            </div>
        </div>
        <div class="footer">
            <p>此邮件由PSM系统自动发送</p>
            <p>{{ current_date }} {{ current_time }}</p>
        </div>
    </div>
</body>
</html>
""",
        body_text="""
{{ month }} 月度项目总结
============================

本月数据统计
-----------
- 项目总数：{{ total_projects }}
- 已完成项目：{{ completed_projects }}
- 完成率：{{ completion_rate }}%

------------------------------------------
此邮件由PSM系统自动发送
{{ current_date }} {{ current_time }}
""",
        variables={
            "month": "月份（YYYY-MM格式）",
            "total_projects": "项目总数",
            "completed_projects": "已完成项目数",
            "completion_rate": "完成率",
            "current_date": "当前日期",
            "current_time": "当前时间"
        },
        description="每月项目完成情况统计"
    )
    
    # 补卡汇总模板
    clock_in_summary_template = EmailTemplate(
        name="补卡数据汇总",
        template_type=EmailTemplateTypeEnum.CLOCK_IN_SUMMARY,
        subject="【补卡汇总】{{ month }} 月补卡数据统计",
        body_html="""
<!DOCTYPE html>
<html>
<head>
    <style>
        body { font-family: Arial, sans-serif; }
        .container { max-width: 800px; margin: 0 auto; padding: 20px; }
        .header { background-color: #FF9800; color: white; padding: 15px; text-align: center; }
        .content { padding: 20px; background-color: #f9f9f9; }
        .user-section { margin: 15px 0; padding: 15px; background: white; border-radius: 5px; }
        .user-name { font-weight: bold; color: #FF9800; margin-bottom: 10px; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        th, td { padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #FFF3E0; }
        .footer { margin-top: 20px; padding: 10px; text-align: center; color: #666; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>{{ month }} 月补卡数据汇总</h2>
        </div>
        <div class="content">
            <p>本月共计 <strong>{{ total_clock_ins }}</strong> 次补卡记录</p>
            
            {% for user in user_statistics %}
            <div class="user-section">
                <div class="user-name">{{ user.username }} (补卡{{ user.count }}次)</div>
                <table>
                    <thead>
                        <tr>
                            <th>日期</th>
                            <th>星期</th>
                            <th>备注</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for record in user.records %}
                        <tr>
                            <td>{{ record.date }}</td>
                            <td>{{ record.weekday }}</td>
                            <td>{{ record.remarks }}</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% endfor %}
        </div>
        <div class="footer">
            <p>此邮件由PSM系统自动发送</p>
            <p>{{ current_date }} {{ current_time }}</p>
        </div>
    </div>
</body>
</html>
""",
        body_text="""
{{ month }} 月补卡数据汇总
============================

本月共计 {{ total_clock_ins }} 次补卡记录

用户统计：
{% for user in user_statistics %}
{{ user.username }} (补卡{{ user.count }}次)
{% for record in user.records %}
  - {{ record.date }} {{ record.weekday }} {{ record.remarks }}
{% endfor %}

{% endfor %}

------------------------------------------
此邮件由PSM系统自动发送
{{ current_date }} {{ current_time }}
""",
        variables={
            "month": "月份（YYYY-MM格式）",
            "total_clock_ins": "补卡总次数",
            "user_statistics": "用户统计列表（包含username, count, records）",
            "current_date": "当前日期",
            "current_time": "当前时间"
        },
        description="每月补卡数据统计汇总"
    )
    
    # 项目到期提醒模板
    project_deadline_template = EmailTemplate(
        name="项目到期提醒",
        template_type=EmailTemplateTypeEnum.PROJECT_DEADLINE,
        subject="【重要提醒】有{{ total_deadline_projects }}个项目即将到期",
        body_html="""
<!DOCTYPE html>
<html>
<head>
    <style>
        body { font-family: Arial, sans-serif; }
        .container { max-width: 800px; margin: 0 auto; padding: 20px; }
        .header { background-color: #f44336; color: white; padding: 15px; text-align: center; }
        .content { padding: 20px; background-color: #f9f9f9; }
        .warning { background-color: #fff3cd; border-left: 4px solid #ffc107; padding: 10px; margin: 10px 0; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #ffebee; color: #d32f2f; }
        .days-remaining { font-weight: bold; }
        .urgent { color: #f44336; }
        .warning-text { color: #ff9800; }
        .footer { margin-top: 20px; padding: 10px; text-align: center; color: #666; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>项目到期提醒</h2>
        </div>
        <div class="content">
            <div class="warning">
                <strong>⚠️ 注意：</strong>以下项目将在15天内到期，请及时跟进！
            </div>
            
            <table>
                <thead>
                    <tr>
                        <th>项目名称</th>
                        <th>负责人</th>
                        <th>截止日期</th>
                        <th>剩余天数</th>
                        <th>当前进度</th>
                    </tr>
                </thead>
                <tbody>
                    {% for project in deadline_projects %}
                    <tr>
                        <td>{{ project.name }}</td>
                        <td>{{ project.employee }}</td>
                        <td>{{ project.deadline }}</td>
                        <td class="days-remaining {% if project.days_remaining <= 3 %}urgent{% elif project.days_remaining <= 7 %}warning-text{% endif %}">
                            {{ project.days_remaining }}天
                        </td>
                        <td>{{ project.progress }}%</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            
            <p style="margin-top: 20px;">请相关负责人尽快处理，确保项目按时完成。</p>
        </div>
        <div class="footer">
            <p>此邮件由PSM系统自动发送</p>
            <p>{{ current_date }} {{ current_time }}</p>
        </div>
    </div>
</body>
</html>
""",
        body_text="""
项目到期提醒
============

⚠️ 注意：以下项目将在15天内到期，请及时跟进！

即将到期的项目：
{% for project in deadline_projects %}
- {{ project.name }}
  负责人：{{ project.employee }}
  截止日期：{{ project.deadline }}
  剩余天数：{{ project.days_remaining }}天
  当前进度：{{ project.progress }}%
  
{% endfor %}

请相关负责人尽快处理，确保项目按时完成。

------------------------------------------
此邮件由PSM系统自动发送
{{ current_date }} {{ current_time }}
""",
        variables={
            "total_deadline_projects": "即将到期项目数",
            "deadline_projects": "项目列表（包含name, employee, deadline, days_remaining, progress）",
            "current_date": "当前日期",
            "current_time": "当前时间"
        },
        description="项目到期前15天的提醒通知"
    )
    
    # 添加到数据库
    templates = [
        weekly_report_template,
        monthly_report_template,
        clock_in_summary_template,
        project_deadline_template
    ]
    
    for template in templates:
        # 检查是否已存在
        existing = EmailTemplate.query.filter_by(
            name=template.name,
            template_type=template.template_type
        ).first()
        
        if not existing:
            db.session.add(template)
    
    db.session.commit()
    print("Email templates initialized successfully!")


if __name__ == "__main__":
    from app import create_app
    app = create_app()
    with app.app_context():
        init_email_templates()