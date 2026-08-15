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

> **重要**：redroid 需要宿主机 Linux 内核提供 `binder` 支持。
> - **推荐**：Linux 宿主机（Ubuntu 22.04+ / Debian 12+），x86_64 或 arm64。
> - **macOS / Windows（Docker Desktop）**：控制器、代理网关、VNC 容器都能跑；redroid 依赖 LinuxKit 内核的 binderfs，较新版本 Docker Desktop 可用，但不保证。先跑 `make check-host` 自检。
> - arm64 宿主机跑 arm 安卓镜像性能最好（Apple Silicon / ARM 服务器）；x86_64 宿主机请用 `redroid:13.0.0-latest`（自带 houdini/ndk-translation 的商业镜像才能跑 arm-only APK）。

宿主机准备（Linux）：

```bash
sudo ./scripts/host-setup.sh    # 加载 binder_linux / ashmem_linux，配置 tun 设备
make check-host                 # 自检
```

## 快速开始

```bash
cp .env.example .env            # 按需修改；HOST_PROJECT_DIR 必须是宿主机绝对路径
make build                      # 构建 gateway / vnc / controller 三个镜像
make pull-android               # 拉取 redroid 安卓镜像
make up                         # 启动控制器
open http://localhost:8000      # Web 控制台
```

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

## 合规提醒

- 仅采集直播间公开可见的信息；不要抓取用户隐私数据。
- 遵守目标平台的用户协议与 `robots` 约定，控制采集频率（默认 60s/次，别调太低）。
- 录屏内容涉及他人著作权，仅用于内部分析，不要二次分发。
- 账号请使用自有账号，风控封号自负。
