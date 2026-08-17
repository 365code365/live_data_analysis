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
| 开机即选配 | 新建云手机是个弹窗：选性能档位（内存/CPU/磁盘）、屏幕档位（分辨率 + 方向）、磁盘容量、出口 IP 区域，实时算出规格摘要 |
| 前后台分离 | 用户前台 `/` 只有云手机、应用市场、任务、数据、录像、套餐；运维与定价在后台 `/admin`（`X-Admin-Token` 保护） |
| 主题可选 | 深色 / 深蓝 / 浅色 / 高对比四套主题，顶栏随时切换，选择记在浏览器本地 |
| 设备控制台 | 一台设备一个页面：干净的屏幕（自带极简投屏页，无多余工具栏）+ 安卓三大金刚 + 常用按键 |
| 画面铺满 | 投屏画面与帧缓冲严格等大，四周不留黑边；竖屏 / 横屏实例都按真实形状铺满浏览器 |
| 声音 | 设备音频经 scrcpy → PulseAudio → ffmpeg 转 mp3 流，网页里直接听，音量可调（网页音量 + 设备媒体音量） |
| 外部文本粘贴 | 浏览器里的文字一键送进安卓，中文可用（原生剪贴板 → ADBKeyboard → input text 三级回退） |
| 应用市场 | 卡片式列表，上传 apk / 粘贴直链 / 内置应用目录三种装法，带进度条；**装完点「打开」直接在当前页面弹出画面操作**，不用跳页 |
| IP 代理 | 每设备一个网关容器，SOCKS5 / HTTP 代理全局透明接管（含 UDP、DNS 防泄漏、kill switch） |
| 直播间监控 | 定时进入直播间，抓标题、主播、在线人数、点赞、弹幕 |
| 商品监控 | 打开购物袋/商品列表，抓商品名、价格、划线价、库存/销量、排序位次 |
| 直播录屏 | 分段 `screenrecord` + ffmpeg 无损合并；**网页内直接预览**（Range 分片，可拖进度），也可下载 |
| 商业化计费 | 套餐/定价后台可配（不同规格不同价格），支付宝 / 微信扫码付款，权益与设备配额自动核算 |
| 数据留存 | SQLite（可换 Postgres）+ 本地文件（截图 / 录像 / UI dump） |

## 运行要求

安卓容器（redroid）需要宿主机 Linux 内核提供 **binder**。这是硬性前提，没有它 redroid 会秒退。

| 宿主 | 安卓容器 | 控制器 / 代理网关 / VNC |
| --- | --- | --- |
| Linux（Ubuntu 22.04+ / Debian 12+，x86_64 或 arm64） | 可用 | 可用 |
| macOS / Windows 的 Docker Desktop | **不可用**（LinuxKit 内核不含 binder，无法通过配置修复） | 可用 |

后台 `/admin` →「宿主自检」会直接给出结论和修复步骤，也可以命令行查：

```bash
make check-host                              # 宿主自检
# 控制器视角的内核能力（后台接口，设了 ADMIN_TOKEN 就要带头）
curl -s -H "X-Admin-Token: $ADMIN_TOKEN" localhost:8000/api/system/host-check
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

安卓镜像不用敲命令：后台 `/admin` →「系统概览」→ 镜像那一行点 **拉取镜像**，带进度条。
也可以照旧 `make pull-android`。

抖音 / 小红书没有稳定官方直链，需要自备安装包：把 APK 拖到前台「应用市场」的
**自有安装包 → 上传并安装**，或放进 `apks/` 目录后在下拉里选。装完点卡片上的「打开」
就能在当前页面操作。

### 典型流程

1. **后台** `/admin` →「代理池」新增代理，例如 `socks5://user:pass@1.2.3.4:1080`，点「验证」确认出口 IP。
2. 前台 `/` →「云手机」新建实例：选套餐、选方向（竖屏 / 横屏，**开机后不能改**）→ 创建即开机。等状态变 `running`。
3. 「应用市场」把抖音 / 小红书装进去，点「打开」在当前页面弹出画面，扫码登录账号。
4. 「监控任务」新建任务：平台 + 直播间标识（抖音 `webcast_id`/短链、小红书 `user_id`/直播链接）+ 采集间隔 + 是否录屏。
5. 「采集数据」看直播间快照与商品变动曲线；「录像回放」在网页里直接看，不用下载。

## 新建云手机：选档位，不填裸数字

前台「云手机 →＋新建云手机」是一个弹窗，四件事都是**固定选项**（服务端 `GET /api/specs` 提供）：

| 选项 | 内容 | 是否硬限制 |
| --- | --- | --- |
| 性能 | 轻量型 2C/2G/16G、标准型 2C/4G/32G（推荐）、高性能 4C/6G/64G、旗舰型 6C/8G/128G | 内存与 CPU 是硬限制，落到容器的 `mem_limit` / `cpu_quota` |
| 屏幕 | 高清竖屏 720×1280、全高清竖屏 1080×1920、高清横屏 1280×720、全高清横屏 1920×1080 | 是，开机参数传给 redroid，**建好之后不能改方向** |
| 磁盘 | 16 / 32 / 64 / 128 / 256 GB（安卓 `/data`） | **看宿主**，见下 |
| 出口 IP 区域 | 直连，或后台代理池里的线路（只显示名称、地区、脱敏 IP） | 是，整机流量透明走该代理 |

为什么做成档位：内存/CPU 填小了直接 OOM，分辨率填奇怪的值 redroid 起不来，而「不同配置不同价格」
也需要一组可枚举的规格才好定价对账。档位定义在 `controller/app/catalogs.py`，改那一个文件即可增删。

选了**套餐**时以套餐规格为准，弹窗会把性能与屏幕两栏置灰并说明原因（避免用低价套餐开高配实例）。

关于磁盘：docker 的 local 卷只有在宿主文件系统开了 project quota（xfs prjquota 或 ext4+prjquota）时
才支持容量限额，否则 `docker volume create --opt size=` 会直接报 `no quota support`。
所以控制器会先尝试带配额创建，失败就退回普通卷，并把结果如实标出来——设备卡上会写
`64GB 磁盘（未限额）`，弹窗里也会提示当前宿主不支持。`GET /api/specs` 里的
`disk_quota_supported` 就是这个探测结果（真的去建一个探针卷试出来的，不是猜的）。

出口 IP 区域这一栏走的是前台接口，所以**只给名称、地区、脱敏 IP（`203.0.*.*`）和占用台数**，
代理的主机、端口、账号密码属于后台内容，永远不出现在前台响应里。

## 界面：用户前台 / 后台管理

三个页面，同一套设计系统与主题：

| 路径 | 给谁用 | 内容 |
| --- | --- | --- |
| `/` | 用户 | 总览、云手机、应用市场、监控任务、采集数据、录像回放、套餐与账单 |
| `/admin` | 管理员 | 系统概览与镜像拉取、设备运维（容器状态/日志/adb shell/UI 树）、代理池、定价管理、订单与权益、支付配置、宿主自检、事件日志 |
| `/console?device=<id>` | 用户 | 单设备控制台：屏幕 + 快捷操作 + 声音 + 粘贴 + 应用 |

后台内容前台一律不可见，边界画在**接口**上而不是只藏按钮：

```bash
ADMIN_TOKEN=随便一串长字符串       # .env，设完重启控制器
```

- 挂了守卫的接口：`/api/proxies/*`、`/api/billing/plans`（写）、`/api/billing/config`、
  `/api/system/{info,images,containers,host-check,platforms,selectors/reload}`、`/api/events`、
  `/api/devices/{id}/{logs,shell,deeplink,ui}`。无令牌返回 401
- 前台开放的：设备增删启停、应用安装、任务、数据、录像、套餐购买
- `/admin` 页面本身是静态文件，进去先要输令牌（存浏览器 localStorage），令牌失效会自动退回登录框
- `ADMIN_TOKEN` **留空时不拦**（方便本地自用），此时前台会露出后台入口，后台顶部也会挂一条
  「后台没有设置访问密码」的警示。对外部署前必须设上

主题在任意页面顶栏的「主题」下拉里切：深色（默认）/ 深蓝 / 浅色 / 高对比。
实现是 CSS 变量 + `<html data-theme>`，只换一组令牌，组件不用改。

## 设备控制台

设备卡片上点「控制台」，或直接访问 `/console?device=<id>`。左边是屏幕，右边是操作区：

- **屏幕**：本项目自带的极简投屏页 `screen.html`（直接调 noVNC 的 RFB 内核），只有画面，没有工具栏和设置面板；
  尺寸按投屏页回报的真实帧缓冲算，竖屏横屏都铺满，四周不留黑边
- **导航键**：屏幕正下方是安卓三大金刚（返回 / 主页 / 最近任务），另有电源、菜单、回车、退格、Tab、Esc
- **声音**：顶栏「声音」开关 + 网页音量条；右侧还有设备侧媒体音量（直接写进安卓）
- **粘贴外部文本**：粘到文本框点一下就进安卓，支持中文
- **应用**：应用目录一键装、直链装、上传装，带进度条；已装应用可启动/卸载
- **其它**：旋转屏幕、截图、开始/停止录屏、唤醒常亮

### 中文粘贴的原理与前提

安卓没有一个到处都好用的文本注入通道，控制台按可靠性依次回退：

1. `cmd clipboard set-text` + `KEYCODE_PASTE` —— 原生，写完会读回来比对确认（redroid/AOSP 上这个服务通常没实现，会自动降级）
2. **ADBKeyboard** 广播 —— 中文可用。在「应用」里一键安装即可，装完控制器会自动把它设为当前输入法
3. `input text` —— 只能 ASCII

注意 ADBKeyboard 是往**当前聚焦的输入框**里注入，所以粘贴前请先在屏幕上点一下目标输入框。

## 声音链路

```
安卓系统混音 → scrcpy --audio-source=output → PulseAudio null sink
            → ffmpeg -f pulse → HTTP mp3 流（设备的音频端口）→ 浏览器 <audio>
```

- VNC 镜像里的 scrcpy 是**源码编译的 3.3.3**：发行版仓库里的 1.25 不支持音频转发，
  而 4.x 起要 SDL3（Ubuntu 24.04 只有 SDL2），3.3.3 是最后一个用 SDL2 且带音频的大版本
- 每台设备占 3 个宿主端口：adb / noVNC / 音频
- 音频流用 `ffmpeg -listen 1`，同一时刻只服务一个听众（控制台场景够用）；
  要多人同时听就在前面挂一层 nginx / icecast 转发
- 不需要声音时把设备的 `enable_audio` 关掉，整条链路不会启动

## 商业化：套餐、定价与支付

前台「套餐与账单」给用户下单，后台 `/admin` →「定价管理」给运营改价改规格
（表格里直接改分辨率、内存、CPU、设备数、价格，失焦即保存），「订单与权益」看收款与额度发放。

- **不同配置不同价格**：套餐规格包含分辨率、DPI、内存、CPU 核数、设备数、任务数、
  是否含代理/录屏/声音、有效天数。用套餐开出来的设备会按规格限制容器资源（`mem_limit` / `cpu_quota`）
- **权益与配额**：支付成功即发放权益（有效期 + 设备名额）。开设备时占用名额，超额直接拒绝；
  到期由调度器自动失效
- **支付渠道**：支付宝当面付（`alipay.trade.precreate`）、微信支付 v3 Native，都是扫码付；
  二维码由服务端渲染成 PNG，前端不引任何二维码库
- **回调安全**：支付宝按 RSA2 验签，微信按 v3 规则验签 + APIv3 密钥 AES-GCM 解密；
  验签不过直接拒绝。金额与订单不符也拒绝，防改价
- **关单保护**：订单超时关闭前会再向渠道确认一次是否已支付，避免「最后一秒付款却被关单」

```bash
# .env 里配置
BILLING_ENABLED=true
BILLING_ENFORCE=false          # 改 true 后「创建设备」必须先买套餐
SITE_BASE_URL=https://你的域名   # 支付回调地址由它拼出来，必须公网可达
PAYMENT_CHANNELS=alipay,wechat  # 本地联调可用 mock
ADMIN_TOKEN=随便一串长字符串      # 设了之后后台定价接口需要 X-Admin-Token
```

需要在支付平台后台填的回调地址：

```
https://你的域名/api/billing/notify/alipay
https://你的域名/api/billing/notify/wechat
```

> **`mock` 通道**：不接真实网关，二维码指向控制台自己的一个链接，打开即视为付款成功。
> 用它可以在没有商户号的情况下把「下单 → 付款 → 发权益 → 按规格开设备」整条链路跑通。
> 上线收款前务必把 `PAYMENT_CHANNELS` 里的 mock 去掉。
>
> 支付宝/微信两个适配器的签名与验签按官方文档实现，但**本项目没有做真实商户联调**，
> 上线前请先用沙箱或小额真实订单验证一遍。

## 目录结构

```
docker/
  proxy-gateway/   出口代理网关镜像（tun2socks + dnsproxy + iptables）
  vnc/             画面镜像（Xvfb + scrcpy + x11vnc + noVNC）
  controller/      控制器镜像（Python + adb + ffmpeg）
controller/app/
  core/            docker 编排、代理、adb/安卓操作、录屏、应用安装、计费、调度
    payments/      支付通道（alipay / wechat / mock，可插拔）
  platforms/       抖音 / 小红书采集适配器 + 可外部覆写的选择器配置
  api/             REST 接口（devices / apps / billing / recordings / data / system）
  web/             前端（原生 HTML/JS，无构建步骤；已 bind mount，改完刷新即生效）
    theme.css      设计令牌 + 四套主题
    style.css      组件层（只用变量，不写死颜色）
    common.js      公共设施：主题、请求、提示、弹窗、令牌、侧栏导航
    index.html     用户前台：总览/云手机/应用市场/任务/数据/录像/套餐
    admin.html     后台管理：系统/设备运维/代理/定价/订单/支付/宿主自检/事件
    console.html   单设备控制台：屏幕 + 快捷操作 + 声音 + 粘贴 + 应用
  apps_catalog.yaml  应用目录（应用商店条目，可用 APPS_CATALOG_FILE 覆盖）
scripts/           宿主机准备与运维脚本
data/              运行期数据（db / 截图 / 录像 / 安卓 /data 卷）
```

## 选择器会过期

App 每次改版，UI 控件都可能变。所有 UI 定位规则都在
`controller/app/platforms/selectors/*.yaml`，支持挂载覆盖（见 `.env` 的 `SELECTORS_DIR`），
改 YAML 即可修复采集，不用改代码、不用重新构建镜像。
调试方式：后台 `/admin` →「设备运维」→「UI 树」，拿到当前页面完整控件树；
改完在「宿主自检」页点 **热加载选择器 YAML** 即生效。

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

App 改版了。用后台「设备运维」里的 **UI 树** 看当前界面真实控件树，
再对着调 `controller/app/platforms/selectors/*.yaml`，改完在后台「宿主自检」页点
**热加载选择器 YAML**（即 `POST /api/system/selectors/reload`），不用重启容器。

### 控制台里没有画面

先用这个脚本把「服务端链路」和「前端页面」分开定位：

```bash
python3 scripts/check-vnc-handshake.py localhost 21001   # 换成设备的 noVNC 端口
```

它用标准库做一次真实的 WebSocket 握手并读 RFB 版本号。看到
`x11vnc 回了 RFB 版本: RFB 003.008` 就说明 websockify → x11vnc 这段没问题，
问题在前端页面或密码参数；反之则是容器侧的问题（继续看下一节和 `docker logs ldm_vnc_1`）。

投屏页用的是本项目自带的 `screen.html`（在 VNC 镜像里，直接调 noVNC 的 RFB 内核），
只渲染画面，没有任何自带 UI。它会把连接状态用 `postMessage` 抛给控制台，
所以认证失败、连接中断这类问题会直接显示原因，而不是给你一片黑。

> 不要用 noVNC 自带的 `vnc_lite.html`：1.3 版里它的缩放参数叫 `scale`（不是 `resize`），
> 而且实测在 iframe 里握手会停在 ProtocolVersion 不往下走（x11vnc 日志是
> `rfbProcessClientProtocolVersion: client gone`，传输 0 字节）。

### 画面一直显示「正在连接」/ 画面不稳定

一条命令体检，它会把「容器侧断链」和「浏览器整页重载」分开：

```bash
./scripts/check-screen-health.sh 1 10     # 设备ID 观察分钟数
```

判读方式：

| 现象 | 含义 |
| --- | --- |
| `scrcpy 启动次数 > 1` | 投屏进程在反复重启，看 `docker exec ldm_vnc_1 cat /tmp/scrcpy.log` |
| VNC 连接次数每 10 秒左右规律增长 | 浏览器在反复重载 iframe（前端问题） |
| 日志里有 `rfbProcessClientProtocolVersion: client gone` | 页面 JS 没走完 RFB 握手 |
| 事件流里 `投屏状态 disconnected` 频繁 | 真的在断链，看网络与容器负载 |

控制台会把浏览器侧的投屏状态回报到服务端（事件流里 `source=screen`），
所以这类问题不用靠猜。设计上做了这些约束来避免「假性不稳定」：

- iframe 的 `src` 只在地址真的变化时才赋值，轮询设备状态不会重载画面
- 断线时**就地重建 RFB**（1s→8s 退避），不做整页 `location.reload()`
- 「正在连接」遮罩只在确实没连上时显示，连上后不会被轮询重新盖上
- 遮罩上的「重试」只让画面重连，不重载整个控制台

### 控制台显示「设备还没准备好」但设备列表是 running

先确认设备是不是真的在跑（`停止` 按钮点过一次就会整组停掉）：

```bash
curl -s localhost:8000/api/devices/1 | python3 -m json.tool | grep -E "status|container_states|screen_ready"
```

`screen_ready` 为 true 才代表安卓容器和画面容器都在跑、画面可连。
设备停了的时候控制台遮罩上会直接给一个「启动设备」按钮，不用回列表页。

容器是被谁停的可以查访问日志与事件流：

```bash
docker logs ldm_controller | grep -E "devices/[0-9]+/(stop|restart|start)"
docker inspect ldm_android_1 --format '{{.State.ExitCode}} oom={{.State.OOMKilled}}'
```

`exit=137 oom=false` 且三个容器依次退出 = 正常的 stop（SIGTERM 后超时被 KILL）；
`oom=true` 才是内存不够，需要给虚拟机加内存或调小套餐规格。

### 画面比手机屏幕大 / 被拉伸

控制台顶栏有「显示」下拉：**适应窗口**（默认，等比缩小，绝不放大）、**100%（1:1 原始像素）**、75%、50%。
右边会实时显示当前是 `720×1280 · 100%` 这样的信息，横屏实例会额外标注「横屏」。

实现上 iframe 尺寸是按「画面真实形状 × 缩放比」算出来的精确像素值，且缩放比上限是 1，
所以不会出现把 720×1280 拉大到窗口尺寸的失真。这个「真实形状」不是数据库里存的分辨率，
而是投屏页回报的 VNC 帧缓冲尺寸，所以横屏实例也能算对。

### 画面只占左上角一小块，四周一大圈黑

已修。原因是 scrcpy 自己算的初始窗口尺寸不可靠：同一个镜像、同样的
`--window-width/--window-height 720x1280`，有时给 720×1280（对），有时给 351×624（约 48.75%）。
它算窗口大小时会参考 X 的可用区域并做 HiDPI 换算，而 Xvfb + 无窗口管理器的环境下这个推断不成立。

现在画面容器里有一个守护协程（entrypoint 的 `screen_fit`），每 2 秒用 `xdotool`
把 scrcpy 窗口钉在 `0,0` 且尺寸等于整块帧缓冲。自查：

```bash
docker exec ldm_vnc_1 sh -c 'DISPLAY=:0 xdpyinfo | grep dimensions; DISPLAY=:0 xwininfo -root -tree | grep scrcpy'
# 两者必须一致，例如 720x1280 与 720x1280+0+0
docker logs ldm_vnc_1 | grep -E '铺满守护|画面为'
```

### 想要横屏画面 / 「旋转屏幕」按钮点了没反应

**安卓实例的屏幕方向在开机时就定死了**：redroid 的分辨率来自启动参数
（`androidboot.redroid_width/height`），容器里没有传感器，实测 `user_rotation`、
`cmd window user-rotation lock`、`set-ignore-orientation-request` 都不会让显示真的转过来
（`dumpsys window` 里的 `mRotation` 始终是 `ROTATION_0`）。

所以要横屏就**新建一台「宽 > 高」的云手机**：前台「云手机 → 新建」里把方向选成「横屏」
（等价于填 1280×720）。画面容器会把帧缓冲设成 1280×720，投屏铺满，浏览器里也按横屏排版。

控制台的「旋转屏幕」按钮现在会回读真实的显示方向：没转成功时弹窗说明原因，
不再报一个假的「已旋转」。

顺带一提，画面容器的帧缓冲是可以在竖/横之间切的（Xvfb 按长边开一块正方形，
再用 `xrandr --fb` 缩到实际方向），所以将来换成支持转屏的安卓镜像时，
转屏后画面形状会自动跟着变，不用改代码。

### 列表刷新时页面在抖 / 预览图一直闪

已修。原来每 15 秒轮询一次就把整个设备网格 `innerHTML` 重写一遍，节点被销毁重建，
预览图重新加载、按钮闪一下，看着就是页面在抖。现在的做法：

- 设备卡片**按 id 就地更新**：只有内容真的变了才改对应的那一块，预览图所在的节点永不重写
- 预览图**先在离屏对象里解码，解好了才换 `src`**，并回收上一张的 blob（顺带修掉了内存泄漏）
- 预览图走自己的、更慢的节拍（10 秒检查、单设备最少间隔 20 秒），跟列表刷新解耦
- 所有列表/表格用 `setHTML`：内容相同直接跳过，不碰 DOM
- 下拉框用 `setOptions`：选项没变不重写，重写后恢复用户已选中的值
- 打开弹窗、光标在输入框里、标签页在后台时**暂停自动刷新**，切回前台补一次
- 预览图容器写死 16:10 比例，图片绝对定位，异步加载不会把下面的内容顶下去

### 屏幕是黑的但 scrcpy 日志正常

先确认是不是安卓自己息屏了：

```bash
curl -s -X POST localhost:8000/api/devices/1/display/keep-awake
```

监控场景屏幕必须常亮。控制器会在设备开机后自动设置常亮，运行期发现息屏也会自动救回来
（`screen_off_timeout` 拉满 + `svc power stayon true`）。scrcpy 自带的 `--stay-awake`
依赖「充电中」状态，redroid 不一定上报，所以不能只靠它。

### 网页里听不到声音

按顺序排查：

```bash
curl -s localhost:8000/api/devices/1 | grep -o '"enable_audio":[a-z]*'   # 设备是否开了声音
docker exec ldm_vnc_1 pactl list short sinks                            # 应看到 ldm ... RUNNING
docker exec ldm_vnc_1 sh -c 'grep -i audio /tmp/scrcpy.log'              # scrcpy 是否转发了音频
docker logs ldm_vnc_1 | grep '^\[audio'                                  # 音频流是否在监听
```

浏览器首次点「声音」可能被自动播放策略拦掉，再点一次即可。
另外音频流同一时刻只接一个听众，别开多个标签页同时听。

### 支付回调收不到 / 订单一直 pending

- 本地没有公网回调时，控制器每 2 分钟会主动向渠道查一次订单状态兜底，
  前端轮询 `GET /api/billing/orders/{order_no}` 也会顺带查一次
- 回调地址必须公网可达且与支付平台后台配置一致，看 `GET /api/billing/config` 里的 `notify_urls`
- 验签失败会返回 400 并在日志里写明原因，不会把订单置为已付

### 代理配了但出口 IP 没变

后台「代理池」里点「验证」，它会起一个一次性网关容器实测出口 IP。
「设备运维」里点「测出口」是在该设备的网关容器里实测。两者不一致时看网关日志：

```bash
curl -s "localhost:8000/api/devices/1/logs?role=gw&tail=100"
```

网关是 fail-closed 的：tun2socks 挂掉时容器直接退出并重启，不会裸奔出网。

## 合规提醒

- 仅采集直播间公开可见的信息；不要抓取用户隐私数据。
- 遵守目标平台的用户协议与 `robots` 约定，控制采集频率（默认 60s/次，别调太低）。
- 录屏内容涉及他人著作权，仅用于内部分析，不要二次分发。
- 账号请使用自有账号，风控封号自负。
