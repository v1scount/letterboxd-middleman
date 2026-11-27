FROM node:22-bookworm-slim AS node

FROM python:3.12-bookworm

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgl1 \
        libusb-1.0-0 \
        libx11-6 \
        libxext6 \
        libxkbcommon0 \
        openjdk-17-jre-headless \
        unzip \
        xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf "$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")" /opt/java-home

ENV JAVA_HOME=/opt/java-home \
    ANDROID_HOME=/opt/android \
    ANDROID_SDK_ROOT=/opt/android \
    ADB=/opt/android/platform-tools/adb \
    PATH="/opt/android/platform-tools:/opt/scrcpy:/opt/java-home/bin:${PATH}"

ARG SCRCPY_VERSION=4.1
ARG SCRCPY_SHA256=ad56ae8bfeedf41e824945c11dbf55fcb092b3e615b9b486f48a50e30d389635
RUN curl -fsSL \
        "https://github.com/Genymobile/scrcpy/releases/download/v${SCRCPY_VERSION}/scrcpy-linux-x86_64-v${SCRCPY_VERSION}.tar.gz" \
        -o /tmp/scrcpy.tar.gz \
    && echo "${SCRCPY_SHA256}  /tmp/scrcpy.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/scrcpy \
    && tar -xzf /tmp/scrcpy.tar.gz -C /opt/scrcpy --strip-components=1 \
    && rm -f /opt/scrcpy/adb \
    && rm /tmp/scrcpy.tar.gz

RUN curl -fsSL https://dl.google.com/android/repository/platform-tools-latest-linux.zip -o /tmp/platform-tools.zip \
    && unzip -q /tmp/platform-tools.zip -d /opt/android \
    && rm /tmp/platform-tools.zip

RUN npm install -g appium \
    && appium driver install uiautomator2 \
    && npm cache clean --force

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

ENV HOST=0.0.0.0 \
    PORT=8787 \
    PYTHONUNBUFFERED=1 \
    APPIUM_SPAWN=true \
    APPIUM_URL=http://127.0.0.1:4723

ENTRYPOINT ["/app/docker-entrypoint.sh"]
