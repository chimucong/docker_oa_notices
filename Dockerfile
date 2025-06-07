FROM python:3.11-slim

# 使用阿里云 Debian 源
RUN rm -f /etc/apt/sources.list && \
    rm -rf /etc/apt/sources.list.d/* && \
    echo "deb http://mirrors.aliyun.com/debian bookworm main contrib non-free" > /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian-security bookworm-security main contrib non-free" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian bookworm-updates main contrib non-free" >> /etc/apt/sources.list

ARG APP_DIR=/app

WORKDIR ${APP_DIR}

#  chrome & chromedriver
RUN apt update && apt-get install -y wget unzip
RUN wget https://storage.googleapis.com/chrome-for-testing-public/137.0.7151.68/linux64/chrome-linux64.zip
RUN wget https://storage.googleapis.com/chrome-for-testing-public/137.0.7151.68/linux64/chromedriver-linux64.zip
RUN unzip chrome-linux64.zip
RUN unzip chromedriver-linux64.zip
RUN rm -rf *.zip
ENV PATH="${APP_DIR}/chrome-linux64:${APP_DIR}/chromedriver-linux64:${PATH}"

# 安装 chrome 依赖包和工具
RUN apt-get install -y curl gnupg fonts-liberation libasound2 libatk-bridge2.0-0 \
    libnspr4 libnss3 libxss1 libappindicator3-1 libxshmfence1 libgbm1 libu2f-udev \
    libvulkan1 xdg-utils ca-certificates 

# 浏览器中文字体
RUN apt install -y fonts-noto-cjk fonts-wqy-zenhei

RUN apt clean && rm -rf /var/lib/apt/lists/*

# 防止 Chrome 无沙箱模式崩溃
ENV DISPLAY=:99
ENV PYTHONUNBUFFERED=1

# 安装 Python 依赖
COPY requirements.txt ./
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r ./requirements.txt

COPY app.py ./
CMD ["python3", "app.py"]
