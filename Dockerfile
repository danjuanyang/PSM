# 使用官方 Python 镜像作为基础镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖，用于编译部分 Python 库，并安装 dos2unix
RUN apt-get update && apt-get install -y \
    build-essential \
    dos2unix \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 安装 gunicorn 用于生产环境
RUN pip install gunicorn

# 复制所有应用代码到工作目录
COPY . .

# 复制并授权启动脚本，并修正换行符
COPY entrypoint.sh .
RUN chmod +x ./entrypoint.sh
RUN dos2unix ./entrypoint.sh

# 声明容器对外暴露的端口
EXPOSE 3456

# 设置启动脚本
ENTRYPOINT ["./entrypoint.sh"]

# 容器启动时传递给 entrypoint.sh 的默认命令
# 使用 gunicorn 启动应用，-w 4 表示4个工作进程，-b 0.0.0.0:3456 表示监听所有网络接口的3456端口
# "run:app" 指的是 run.py 文件中的 app 对象
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:3456", "run:app"]