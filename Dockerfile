# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

# 时区数据：tzdata 是纯 Python 包，供 zoneinfo 使用（比 apt 安装 tzdata 更省层）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    GOLD_HOST=:: \
    GOLD_PORT=8765 \
    GOLD_NO_BROWSER=1

RUN pip install --no-cache-dir --no-compile tzdata

WORKDIR /app

# 依赖固定且极少，先拷 requirements 思维这里不需要；直接拷应用文件
COPY gold_server.py ./
COPY home/ ./home/
COPY gold/ ./gold/
COPY ashare/ ./ashare/

# 导航首页 + 双页面：/ 导航，/gold/ 黄金，/ashare/ A股成交额
ENV PAGES=/gold/:gold,/ashare/:ashare

# 容器里没有浏览器，服务用非 root 用户运行
RUN useradd --system --create-home --uid 10001 gold && chown -R gold:gold /app
USER gold

EXPOSE 8765

HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('GOLD_PORT','8765')+'/api/ping',timeout=5)"

CMD ["python", "gold_server.py", "--no-browser"]
