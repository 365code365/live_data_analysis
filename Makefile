SHELL := /bin/bash
-include .env
export

COMPOSE ?= docker compose

# Docker Desktop 会继承 macOS 系统代理。如果那个代理端口没进程监听（代理软件关了），
# 构建期所有 apt/pip 请求都会先撞死代理再重试，表现为「构建极慢 + 随机 500」。
# 默认用空的 proxy build-arg 覆盖掉；配合国内镜像源不需要代理。
# 确实要走代理（比如用官方源）时：make build BUILD_NO_PROXY=0
BUILD_NO_PROXY ?= 1
ifeq ($(BUILD_NO_PROXY),1)
PROXY_ARGS := --build-arg http_proxy= --build-arg https_proxy= \
              --build-arg HTTP_PROXY= --build-arg HTTPS_PROXY= --build-arg no_proxy=*
else
PROXY_ARGS :=
endif

.PHONY: help build build-gateway build-vnc build-controller pull-android up down restart logs shell check-host check-proxy clean-devices dev

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

build: build-gateway build-vnc build-controller ## 构建全部镜像

build-gateway: ## 构建代理网关镜像
	docker build $(PROXY_ARGS) -t $${GATEWAY_IMAGE:-ldm/proxy-gateway:latest} docker/proxy-gateway

build-vnc: ## 构建 VNC 画面镜像（scrcpy 依赖 200+ 个包，首次约 3-8 分钟）
	docker build $(PROXY_ARGS) --build-arg APT_MIRROR=$${APT_MIRROR-mirrors.aliyun.com} \
		-t $${VNC_IMAGE:-ldm/android-vnc:latest} docker/vnc

build-controller: ## 构建控制器镜像
	docker build $(PROXY_ARGS) \
		--build-arg APT_MIRROR=$${APT_MIRROR-mirrors.aliyun.com} \
		--build-arg PIP_INDEX_URL=$${PIP_INDEX_URL-https://mirrors.aliyun.com/pypi/simple/} \
		-f docker/controller/Dockerfile -t ldm/controller:latest .

check-proxy: ## 检查宿主代理是否是构建慢的元凶
	@bash scripts/check-build-proxy.sh

pull-android: ## 拉取 redroid 安卓镜像
	docker pull $${REDROID_IMAGE:-redroid/redroid:13.0.0_64only-latest}

up: ## 启动控制器
	mkdir -p data/db data/screenshots data/recordings data/dumps data/android apks
	$(COMPOSE) up -d
	@echo "控制台: http://localhost:$${CONTROLLER_PORT:-8000}"

down: ## 停止控制器（不动设备容器）
	$(COMPOSE) down

restart: down up ## 重启控制器

logs: ## 跟随控制器日志
	$(COMPOSE) logs -f controller

shell: ## 进入控制器容器
	docker exec -it ldm_controller bash

check-host: ## 宿主机环境自检
	@bash scripts/host-setup.sh --check

clean-devices: ## 强制清理所有设备容器（不删 /data 卷目录）
	@bash scripts/clean-devices.sh

dev: ## 本地直跑控制器（需自备 python3.11 + adb + ffmpeg）
	cd controller && python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
