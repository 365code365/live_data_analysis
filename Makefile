SHELL := /bin/bash
-include .env
export

COMPOSE ?= docker compose

.PHONY: help build build-gateway build-vnc build-controller pull-android up down restart logs shell check-host clean-devices dev

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

build: build-gateway build-vnc build-controller ## 构建全部镜像

build-gateway: ## 构建代理网关镜像
	docker build -t $${GATEWAY_IMAGE:-ldm/proxy-gateway:latest} docker/proxy-gateway

build-vnc: ## 构建 VNC 画面镜像
	docker build -t $${VNC_IMAGE:-ldm/android-vnc:latest} docker/vnc

build-controller: ## 构建控制器镜像
	$(COMPOSE) build controller

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
