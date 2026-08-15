# Live Data Analysis — 安卓容器直播间监控平台

基于 **Docker + 安卓容器（redroid）+ VNC** 的抖音 / 小红书直播间监控平台。
每个"设备"是一个独立的安卓实例，拥有独立的出口 IP（代理网关）、独立的 VNC 画面、独立的采集与录屏任务。

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Browser  ──►  Controller (FastAPI + Web 控制台)  ──►  Docker Engine      │
│                    │  APScheduler 采集调度                                │
│                    │  adb / uiautomator2                                  │
└────────────────────┼─────────────────────────────────────────────────────┘
                     │  每个设备一组容器（共享同一 network namespace）
        ┌────────────┴──────────────────────────────────────────┐
        │  gw-<id>       tun2socks + dnsproxy + iptables         │  ← 出口 IP / IP 代理
        │   ├── android-<id>   redroid 安卓实例 (adb :5555)      │  ← 抖音 / 小红书 App
        │   └── vnc-<id>       Xvfb + scrcpy + x11vnc + noVNC    │  ← 浏览器看屏 / 手动操作
        └───────────────────────────────────────────────────────┘
```

## 能力

| 能力 | 说明 |
| --- | --- |
| 安卓虚拟机 | redroid 容器化安卓，单机可跑多实例，`/data` 持久化（保留登录态） |
| VNC 远程桌面 | 浏览器内 noVNC 直接看屏、点屏（扫码登录、过验证码全靠它） |
| IP 代理 | 每设备一个网关容器，SOCKS5 / HTTP 代理全局透明接管（含 UDP、DNS 防泄漏） |
| 直播间监控 | 定时进入直播间，抓标题、主播、在线人数、点赞、弹幕 |
| 商品监控 | 打开购物袋/商品列表，抓商品名、价格、划线价、库存/销量、排序位次 |
| 直播录屏 | 分段 `screenrecord` + ffmpeg 无损合并，长时间录制不丢帧 |
| 数据留存 | SQLite（可换 Postgres）+ 本地文件（截图 / 录像 / UI dump） |

## 运行要求

安卓容器（redroid）需要宿主机 Linux 内核提供 **binder**。这是硬性前提，没有它 redroid 会秒退。

| 宿主 | 安卓容器 | 控制器 / 代理网关 / VNC |
| --- | --- | --- |
| Linux（Ubuntu 22.04+ / Debian 12+，x86_64 或 arm64） | 可用 | 可用 |
| macOS / Windows 的 Docker Desktop | **不可用**（LinuxKit 内核不含 binder，无法通过配置修复） | 可用 |

控制台首页会自动检测并给出提示，也可以直接查：

```bash
make check-host                              # 宿主自检
curl -s localhost:8000/api/system/host-check # 控制器视角的内核能力
```

Linux 宿主准备：

```bash
sudo ./scripts/host-setup.sh    # 加载 binder_linux / ashmem_linux，准备 /dev/net/tun
```

Ubuntu 上如果 `modprobe binder_linux` 报找不到模块，先装 `linux-modules-extra-$(uname -r)`。

### 在 Mac 上跑完整链路（已验证）

一条命令开一台带 binder 的 Ubuntu 虚拟机，项目目录会挂载进去，端口自动转发回 macOS：

```bash
brew install lima      # 已装可跳过
make lima-up           # 建虚拟机 + 装内核模块 + 装 docker，并验证 binder
make lima-deploy       # 在虚拟机内构建镜像并启动全部服务
```

跑完在 Mac 浏览器直接开 `http://localhost:8000`（或你在 `.env` 里设的 `CONTROLLER_PORT`）。
实测环境：Ubuntu 24.04 / 内核 6.8.0-137-generic，binder 可用，redroid Android 13 正常开机、
adb 可连、noVNC 有画面、录屏产出可播放的 mp4。

常用运维：

```bash
make lima-shell        # 进虚拟机
make lima-stop         # 停虚拟机
make lima-delete       # 删虚拟机（不动项目文件）
```

两个注意点：

- **端口别撞**。lima 把虚拟机里的端口转发到 macOS 的 localhost，如果 Mac 上已有进程占用
  8000（`lsof -nP -iTCP:8000 -sTCP:LISTEN` 查），改 `.env` 里的 `CONTROLLER_PORT`。
  Mac 本机如果也起过一份控制器，先 `docker compose down`。
- **数据别放共享目录**。`make lima-deploy` 会自动把 `DATA_HOST_DIR` 指到虚拟机本地盘
  `/var/lib/ldm/data`，因为 SQLite 在 virtiofs 共享目录上的文件锁不可靠。

只在 Mac 本机开发也可以：控制器、代理网关、VNC 容器都能正常跑，代理链路和选择器规则都能调通，只是没法启动安卓实例。

镜像选择：arm64 宿主用 `redroid/redroid:13.0.0_64only-latest`；x86_64 宿主用 `redroid/redroid:13.0.0-latest`
（跑 arm-only 的 APK 需要带 houdini / ndk-translation 的镜像）。

## 快速开始

```bash
cp .env.example .env            # 按需修改
make build                      # 构建 gateway / vnc / controller 三个镜像
make up                         # 启动控制器
open http://localhost:8000      # Web 控制台
```

安卓镜像不用敲命令：控制台首页「安卓镜像」那一行点 **立即拉取**，带进度条。
也可以照旧 `make pull-android`。

把抖音 / 小红书 APK 放进 `apks/` 目录（文件名建议 `douyin.apk`、`xiaohongshu.apk`），
控制台 → 设备卡片 → **安装 APK** 即可推进容器。

### 典型流程

1. 控制台「代理」页新增代理，例如 `socks5://user:pass@1.2.3.4:1080`，点「测试」确认出口 IP。
2. 「设备」页新建设备，选择分辨率与代理 → 启动。等状态变 `running`。
3. 点设备卡上的 **VNC**，在浏览器里手动装 APK、登录账号（扫码 / 短信）。
4. 「任务」页新建监控任务：平台 + 直播间标识（抖音 `webcast_id`/短链、小红书 `user_id`/直播链接）+ 采集间隔 + 是否录屏。
5. 「数据」页看直播间快照与商品变动曲线；「录像」页下载 mp4。

## 目录结构

```
docker/
  proxy-gateway/   出口代理网关镜像（tun2socks + dnsproxy + iptables）
  vnc/             画面镜像（Xvfb + scrcpy + x11vnc + noVNC）
  controller/      控制器镜像（Python + adb + ffmpeg）
controller/app/
  core/            docker 编排、代理管理、设备池、adb、录屏、调度
  platforms/       抖音 / 小红书采集适配器 + 可外部覆写的选择器配置
  api/             REST 接口
  web/             控制台前端（原生 HTML/JS + noVNC iframe）
scripts/           宿主机准备与运维脚本
data/              运行期数据（db / 截图 / 录像 / 安卓 /data 卷）
```

## 选择器会过期

App 每次改版，UI 控件都可能变。所有 UI 定位规则都在
`controller/app/platforms/selectors/*.yaml`，支持挂载覆盖（见 `.env` 的 `SELECTORS_DIR`），
改 YAML 即可修复采集，不用改代码、不用重新构建镜像。
调试方式：控制台设备卡 →「UI Dump」，拿到当前页面完整控件树。

## 常见问题

### 构建极慢，或 apt 随机报 500 / Unable to connect

大概率是宿主代理的问题：Docker Desktop 会继承 macOS 系统代理，代理软件一关，
端口就没人监听了，而构建期每个 apt/pip 请求都要先撞一次死代理再重试。

```bash
make check-proxy      # 一眼看出是不是这个原因
```

本项目已经默认规避：`make build` 会传入空的 `http_proxy/https_proxy` build-arg，
并默认使用阿里云镜像源。所以正常情况下你不需要做任何事。

```bash
make build-vnc APT_MIRROR=mirrors.tuna.tsinghua.edu.cn   # 换源
make build BUILD_NO_PROXY=0                              # 确实要走代理时
make build-vnc APT_MIRROR=                               # 用官方源
```

另外，VNC 镜像里 `scrcpy` 会带进整套 ffmpeg + SDL2，一共 200 多个包，
首次构建 3-8 分钟是正常的；已配置 apt 缓存挂载，重试不会重新下载。

### 设备起不来 / 状态一直 starting

按顺序看：

```bash
make check-host                              # binder、/dev/net/tun 是否就绪
curl -s localhost:8000/api/devices/1/status  # 三个容器分别是什么状态
curl -s "localhost:8000/api/devices/1/logs?role=android&tail=200"
```

redroid 常见问题是宿主内核没有 binder，见上面「运行要求」。

### noVNC 能连上但是黑屏

看 scrcpy 用的是哪个渲染器：

```bash
docker exec ldm_vnc_1 sh -c 'grep -i renderer /tmp/scrcpy.log'
```

必须是 `Renderer: software`。scrcpy 默认用 OpenGL，画面进的是 GLX 缓冲，
x11vnc 抓不到，结果就是 scrcpy 日志一切正常但 noVNC 全黑。
VNC 镜像里已经强制 `SDL_RENDER_DRIVER=software` + `--render-driver=software`。

如果 scrcpy 根本没起来，看画面容器日志：

```bash
docker logs ldm_vnc_1 | grep '^\[vnc'
```

已知并已修掉的两个坑（升级镜像即可）：

- `docker restart` 后 `/tmp/.X0-lock` 残留导致 Xvfb 拒绝启动，整条链路卡死
- adb 34 的 mDNS 自动发现在受限 netns 里会把 `adb start-server` 挂死；
  另外 redroid 的 adbd 监听 5555，落在 adb 的模拟器端口区间内，
  用 `127.0.0.1` 连会被识别成 `emulator-5554 offline`，现在改用容器自身的非回环地址

### 采不到商品 / 采到的字段是空的

App 改版了。用控制台设备卡上的 **UI Dump** 看当前界面真实控件树，
再对着调 `controller/app/platforms/selectors/*.yaml`，改完调用
`POST /api/system/selectors/reload` 热加载，不用重启容器。

### 代理配了但出口 IP 没变

点代理列表里的「测试」，它会起一个一次性网关容器实测出口 IP。
设备侧点「查出口 IP」是在该设备的网关容器里实测。两者不一致时看网关日志：

```bash
curl -s "localhost:8000/api/devices/1/logs?role=gw&tail=100"
```

网关是 fail-closed 的：tun2socks 挂掉时容器直接退出并重启，不会裸奔出网。

## 合规提醒

- 仅采集直播间公开可见的信息；不要抓取用户隐私数据。
- 遵守目标平台的用户协议与 `robots` 约定，控制采集频率（默认 60s/次，别调太低）。
- 录屏内容涉及他人著作权，仅用于内部分析，不要二次分发。
- 账号请使用自有账号，风控封号自负。
