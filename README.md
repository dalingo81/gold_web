# 黄金实时看盘 · Gold Dashboard

一个零依赖的实时黄金价格看板：**日K线 + 均线 + MACD**，价格可在 **人民币/克** 与 **美元/盎司** 之间一键切换，自动刷新。

后端是纯 Python 标准库写的行情聚合服务（不需要 pip 安装任何包），前端用本地部署的 ECharts 渲染，**打包成单个 Docker 镜像即可放到 NAS / 服务器 / 云主机上常驻**。

> 数据：伦敦金现货 XAU/USD、上海金 Au(T+D)、美元人民币实时汇率。人民币报价 = 国际金价 ÷ 31.1034768 × 实时汇率。

---

## 功能

| 类别 | 说明 |
| --- | --- |
| 实时报价 | 现价、涨跌额/幅、今开/昨收/最高/最低、日内区间位置条；价格跳动时红涨绿闪 |
| 单位切换 | 人民币 / 克 ⇄ 美元 / 盎司，卡片与图表 Y 轴同步换算 |
| 日K图 | 蜡烛图 + MA5/10/20/60 + MACD 副图 + 当前价虚线 + 缩放拖拽 + 十字光标浮层 |
| 周期切换 | 近 1 月 / 3 月 / 6 月 / 1 年 / 3 年 / 全部（约 6 年） |
| 刷新策略 | 3/5/10/30/60 秒可选或暂停；页面切到后台自动暂停；下一次刷新倒计时可见 |
| 辅助面板 | 上海金 Au(T+D) 报价与内外盘溢价、逐层换算明细、数据源健康状态 |
| 双运行模式 | 容器/本机服务模式（完整数据）或直接双击 HTML（降级直连公开接口） |
| IPv6 支持 | 双栈监听 `GOLD_HOST=::`，同一 socket 同时服务 IPv4/IPv6，浏览器用 `http://[地址]:8765` 打开 |

---

## 目录结构

```
.
├── Dockerfile            # 镜像定义（python:3.12-slim-bookworm，无 pip 业务依赖）
├── docker-compose.yml    # 一键编排
├── .dockerignore
├── README.md
├── gold_server.py        # 行情聚合服务（仅 Python 标准库）
├── gold/
│   ├── index.html        # 看盘页面（含全部逻辑）
│   └── echarts.min.js    # ECharts 5.5.1，随包离线部署
└── 启动黄金看盘.bat       # 不用 Docker 时，Windows 双击启动
```

---

## 快速开始

### 方式 A：Docker Compose（推荐）

```bash
docker compose up -d --build
```

打开 <http://localhost:8765>。

常用操作：

```bash
docker compose logs -f          # 看日志
docker compose ps               # 看健康状态（healthy 才正常）
docker compose down             # 停止并删除容器
docker compose up -d --build    # 改了代码后重新构建
```

### 方式 B：手动 build + run

```bash
docker build -t gold-dashboard:1.0 .

docker run -d \
  --name gold-dashboard \
  --restart unless-stopped \
  -p 8765:8765 \
  -e TZ=Asia/Shanghai \
  -e GOLD_HOST=0.0.0.0 \
  gold-dashboard:1.0
```

查看状态与日志：

```bash
docker ps                      # STATUS 列显示 (healthy)
docker logs -f gold-dashboard
docker stop gold-dashboard && docker rm gold-dashboard
```

### 方式 B2：离线镜像包导入（无需构建、无需编译环境）

仓库/发布目录附带一个打好包的镜像文件 **`gold-dashboard-1.0.tar.gz`**（约 43 MB，linux/amd64），
适合 **NAS 上不方便拉 base 镜像、或 build 老失败**的场景——导入后直接跑：

```bash
docker load -i gold-dashboard-1.0.tar.gz      # 导入镜像，tag 为 gold-dashboard:1.0

docker run -d \
  --name gold-dashboard \
  --restart unless-stopped \
  -p 8765:8765 \
  -e TZ=Asia/Shanghai \
  gold-dashboard:1.0
```

想用 Compose 编排（而不是裸 `docker run`），仓库里带了专为镜像包准备的
**`docker-compose.load.yml`**（纯 `image:` 引用，不含 build）：

```bash
docker load -i gold-dashboard-1.0.tar.gz
docker compose -f docker-compose.load.yml up -d      # 停止：... down
```

飞牛 fnOS 图形界面也一样：Compose → 新增项目 → 把 `docker-compose.load.yml` 的内容粘进去，
**不需要**再传源码文件（镜像已通过「镜像 → 导入」或 SSH `docker load` 进系统）。

> 该镜像由离线构建器（`build_image_offline.py`，纯 Python 标准库）从公共镜像源抓取
> `python:3.12-slim-bookworm` 的官方层 + 应用层拼装而成，无需 Docker daemon / docker build。
> 配置与「方式 A/B」等价：工作目录 `/app`、启动命令、`GOLD_HOST=::` 双栈、8765 端口、
> 健康检查全部一致；唯一差异是容器内以 `root` 运行（离线构建不创建专用用户），
> 单用户私有看盘场景无实际风险。
> 加载前可以先用 `sha256sum`/`Get-FileHash` 校验文件完整性。

### 方式 C：不用 Docker（Windows / macOS / Linux 本机）

```bash
python gold_server.py           # 默认只监听 127.0.0.1:8765，自动打开浏览器
python gold_server.py --no-browser   # 不开浏览器
```

Windows 用户直接双击 **`启动黄金看盘.bat`** 即可。要求 Python 3.9+，**无需安装任何第三方包**。

---

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `GOLD_HOST` | `127.0.0.1` | 监听地址。取值见下表；**容器里默认 `::`（双栈）** |
| `GOLD_PORT` | `8765` | 监听端口 |
| `TZ` | `Asia/Shanghai` | 影响页面"更新时间"与 K 线日期的显示时区 |
| `GOLD_NO_BROWSER` | 空 | 设为任意非空值则不尝试自动打开浏览器（容器已默认开启） |

### `GOLD_HOST` 取值

| 值 | 效果 |
| --- | --- |
| `127.0.0.1` | 仅本机可见（Windows 双击 bat 的默认） |
| `0.0.0.0` | 监听所有 IPv4 网卡，局域网可访问 |
| `::` | **IPv4 + IPv6 双栈**（推荐）：一个 socket 同时接受两种协议的连接，容器内默认 |
| `::1` | 仅 IPv6 环回，只本机可见 |
| `[240e:xx::x]` 等具体地址 | 只监听该 IPv6 地址，方括号可写可不写 |

命令行参数 `--no-browser` 与环境变量等价。

> 改容器端口时注意两处：`-p 宿主机端口:容器端口` 的右侧要和 `GOLD_PORT` 一致。

---

## HTTP 接口

| 路径 | 说明 |
| --- | --- |
| `GET /` | 看盘页面 |
| `GET /api/ping` | 轻量探活，不触发任何外部请求，用于健康检查/反代监控 |
| `GET /api/quote` | 实时报价，服务端 3 秒缓存 |
| `GET /api/kline?days=N` | 日K线，`days` 省略或 0 表示全部（约 6 年），服务端 5 分钟缓存 |

### `/api/ping`

```json
{"ok": true, "pong": true, "time": "2026-09-18 16:20:00", "tz": "Asia/Shanghai", "uptime": 128}
```

### `/api/quote`

```json
{
  "ok": true,
  "ts": 1789719453,
  "server_time": "2026-09-18 16:17:33",
  "xau": {
    "price": 4391.89, "prev_close": 4341.62, "open": 4343.47,
    "high": 4399.49, "low": 4334.23,
    "change": 50.27, "change_pct": 1.1579,
    "quote_time": "16:17:00", "quote_date": "2026-09-18",
    "price_source": "gold-api"
  },
  "fx": {"usdcny": 6.697, "bid": 6.6965, "ask": 6.7012, "source": "sina", "time": "16:11:53"},
  "autd": {"name": "黄金延期", "price": 946.61, "prev_close": 934.59,
           "open": 938.0, "high": 948.48, "low": 935.03, "change_pct": 1.2861,
           "time": "2026-09-18 15:30:02"},
  "derived": {"cny_per_gram": 946.1015, "usd_per_gram": 141.2029},
  "errors": {},
  "oz_to_gram": 31.1034768
}
```

`errors` 非空表示有数据源降级，键名为源标识、值为异常信息；页面右下角"数据源状态"会同步显示。

### `/api/kline?days=2`

```json
{"ok": true, "count": 2, "updated": "2026-09-18 16:12:16",
 "data": [{"date": "2026-09-17", "open": 4260.49, "high": 4381.14, "low": 4257.4, "close": 4341.62}]}
```

单位为美元/盎司。

---

## 数据源

| 数据 | 来源 | 备注 |
| --- | --- | --- |
| 伦敦金现货 XAU/USD | 新浪财经 `hf_XAU`（带当日开高低与昨收） | 主源，需服务端带 Referer 请求，浏览器无法直连 |
| 现货兜底报价 | `api.gold-api.com` | 无需 Key，CORS 开放 |
| 上海金 Au(T+D) | 新浪财经 `SGE_AUTD` | 人民币/克，含夜盘 |
| 美元人民币 | 新浪财经 `USDCNY`，兜底 `open.er-api.com` | 取买卖中价 |
| 日K线 | 新浪全球期货日K接口 | 一次返回约 20 年 / 5000+ 条 |

**更新机制**：报价按页面设定的频率拉取（默认 5 秒），服务端 3 秒缓存避免打到上游；日K线服务端 5 分钟缓存，前端每 5 分钟拉一次增量，当日那根 K 线由实时价在本地滚动刷新。

换算：`1 金衡盎司 = 31.1034768 克`。实测国际金折算价与上海金 Au(T+D) 通常相差不到 1 元/克，两者互为交叉校验。

---

## IPv6 支持

服务用的是 **IPv6 双栈 socket**（`IPV6_V6ONLY=0`）：绑定 `::` 时同一个 socket 同时接受 IPv6 与 IPv4 连接，不需要开两个进程。若系统不支持双栈，会自动降级为 IPv4 并在启动日志里写明。

**浏览器地址必须给 IPv6 加方括号**（冒号会和端口冲突）：

```
http://[240e:47e:a10:7da2:2403:f340:30b6:511]:8765
```

启动时会自动列出本机可用的 IPv4 / IPv6 访问地址，直接复制即可：

```
 监听： :::8765　[IPv6 双栈（同时接受 IPv4）]
 访问地址：
   本机    http://localhost:8765
   局域网  http://192.168.8.103:8765
   IPv6    http://[240e:47e:...]:8765   ← 浏览器地址必须带方括号
```

### 本机 / 容器里怎么开

```bash
# 本机 Python
export GOLD_HOST=::          # Windows: set GOLD_HOST=::
python gold_server.py --no-browser

# Docker run：-p [::]:8765:8765 才会把端口映射到宿主机 IPv6
docker run -d --name gold-dashboard --restart unless-stopped \
  -p 8765:8765 -p "[::]:8765:8765" \
  -e TZ=Asia/Shanghai -e GOLD_HOST=:: gold-dashboard:1.0
```

### 三种部署形态怎么选

| 方案 | 做法 | 适用 |
| --- | --- | --- |
| **host 网络**（最省事） | compose 里 `network_mode: host` + `GOLD_HOST=::` | NAS / 家庭服务器 / Linux。容器直接用宿主机网络栈，IPv4、IPv6 全部直连，没有端口映射的坑。**Windows / macOS 的 Docker Desktop 不支持，会静默失败** |
| **桥接 + IPv6 映射** | ports 加 `"[::]:8765:8765"`，且 daemon 开启 IPv6 | 常规 Linux 服务器。需在 `/etc/docker/daemon.json` 加 `"ipv6": true, "ip6tables": true` 并重启 docker |
| **反向代理（生产推荐）** | 容器只监听 IPv4/回环，由 Nginx / Caddy 同时监听 IPv4+IPv6 转发 | 要 HTTPS、域名、鉴权的场景。此时容器 `ports` 只暴露到 `127.0.0.1`，公网一律走反代 |

### 验证与排错

```bash
curl -6 http://[::1]:8765/api/ping     # 容器内：确认 IPv6 栈通
curl -4 http://127.0.0.1:8765/api/ping # 确认 IPv4 栈通（双栈时两者都应 200）
curl -6 "http://[你的公网IPv6]:8765/api/ping"
```

| 现象 | 原因与处理 |
| --- | --- |
| 启动日志显示"不支持 IPv6 双栈，降级为 IPv4" | 内核/systemctl 禁用了 IPv6（如 `ipv6.disable=1`）。先恢复系统 IPv6 |
| IPv4 通、IPv6 不通 | 防火墙/安全组没放行 IPv6 的 8765（Windows 防火墙入站规则、云厂商安全组都要单独配 IPv6） |
| 容器内 IPv6 通、宿主机外访问不了 | Docker 端口映射默认只给 IPv4，加 `-p "[::]:8765:8765"` 或改用 host 网络 |
| 浏览器报无法访问 | 地址漏了方括号，或用了临时隐私地址 / `fe80::` 链路本地地址（跨网段无效，要用全局单播地址） |
| 只有 `fe80::` 开头的地址 | 本机没拿到 IPv6 全局地址（运营商/路由器未下发），先确保访问端自己也有 IPv6 |

---

## 部署到飞牛 NAS（fnOS 图形界面）

全程不用敲命令也能完成；**第 1 步用 SSH 拉代码最省事**，不想开 SSH 就用文件管理器手动传。

### 第 1 步：把项目文件放到 NAS 上

推荐目录：`/vol1/1000/docker/gold-dashboard`（`vol1` 换成你实际的存储空间，`1000` 是当前用户目录）。

**方式 A：SSH 一行搞定（推荐）**

1. fnOS 桌面 → **系统设置** → **SSH** → 开启 SSH。
2. 用任意 SSH 客户端连上 NAS，执行：

```bash
mkdir -p /vol1/1000/docker && cd /vol1/1000/docker
git clone https://github.com/dalingo81/gold_web.git gold-dashboard
cd gold-dashboard
ls            # 应看到 Dockerfile、docker-compose.yml、gold_server.py、gold/
```

**方式 B：图形界面手动上传**

在 `/vol1/1000/docker/gold-dashboard` 下依次建好路径并上传这 4 项：

```
Dockerfile
docker-compose.yml        ← 也可以在 Compose 面板里粘贴生成，不必上传
gold_server.py
gold/index.html
gold/echarts.min.js       ← 注意要先建 gold 子目录
```

> **务必确认 `gold/` 子目录存在且里面有两个文件**，否则容器起来了也打不开页面（服务启动时就会提示缺少 index.html）。

### 第 2 步：Compose 面板创建项目

fnOS 桌面 → 打开 **Docker** 应用：

- 较新版本：左侧 **Compose** → 右上角 **新增项目**
- 部分版本：**容器** → 右上角 **添加** → 弹窗里切到 **docker-compose** 标签

填写四项：

| 字段 | 填什么 |
| --- | --- |
| 项目名称 | `gold-dashboard` |
| 路径 | 第 1 步的目录 `/vol1/1000/docker/gold-dashboard` |
| 来源 | 选「创建 docker-compose.yml」（或「上传」，内容一样即可） |
| 配置内容 | 下面那段 YAML |

```yaml
services:
  gold:
    build: .                      # 用上面的路径作为构建上下文
    image: gold-dashboard:1.0
    container_name: gold-dashboard
    restart: unless-stopped
    ports:
      - "8765:8765"
      # 需要 IPv6 访问再加一行（要求 daemon 已开 IPv6，一般要在 NAS 上改 daemon.json）：
      # - "[::]:8765:8765"
    environment:
      TZ: Asia/Shanghai
      GOLD_HOST: "::"
      GOLD_PORT: 8765
```

勾选 **创建项目后立即启动** → **确定**。

首次会自动拉 `python:3.12-slim` 并构建，通常 1～3 分钟。看到状态变成 **running / healthy** 就成了。

### 第 3 步：访问

| 场景 | 地址 |
| --- | --- |
| 局域网 | `http://<NAS内网IP>:8765` |
| NAS 本机 | `http://localhost:8765` |
| IPv6 | `http://[240e:xxxx:xxxx::xxxx]:8765` ← **方括号不能省** |

手机用同一 WiFi 直接开就行；外网访问请走 fnOS 自带的反向代理/域名访问，**不要把 8765 直接映射到公网**。

### 飞牛上常见的坑

| 现象 | 处理 |
| --- | --- |
| 构建卡在拉取 `python:3.12-slim` | 国内网络常态。Docker → **镜像仓库** → 右上角**设置** → **加速源设置**，填一个可用的加速地址后重试（加速源随时可能失效，以当时能用为准） |
| 容器起来了但页面打不开 | 90% 是 `gold/` 目录没传。看容器日志：`docker logs gold-dashboard`，会打印"缺少页面文件" |
| 提示端口被占用 | 改 YAML 的 `ports` 左侧，比如 `"9000:8765"`，用 `http://NAS_IP:9000` 访问 |
| IPv6 地址打不开 | 默认端口映射只给 IPv4，加 `"[::]:8765:8765"` 或改用 host 网络；详见 [IPv6 支持](#ipv6-支持) |
| 更新到新版本 | `cd` 到目录执行 `git pull` → Compose 面板里**重新构建/重启**该项目；或者直接删项目重来 |
| 面板是否支持 `build:` | 支持。fnOS 的 Compose 就是标准 docker compose，会在「路径」目录下执行构建 |

---

## 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 浏览器打不开 `localhost:8765` | 没监听对外地址（检查 `GOLD_HOST`）、端口没映射出来，或容器还没起来：`docker compose ps` 看状态 |
| IPv6 地址访问不了 | 见上面的 [IPv6 支持](#ipv6-支持)：地址要带方括号，且 Docker 需要显式 IPv6 映射或 host 网络 |状态 |
| 页面正常但显示"行情获取失败" | 容器访问不到外网，或上游临时限流。看日志 `docker compose logs -f`，一般会持续重试自动恢复 |
| K 线不出来 | 需要访问 `stock2.finance.sina.com.cn`；若被网络策略拦截，页面会给出明确提示 |
| 更新时间差 8 小时 | `TZ` 未设为 `Asia/Shanghai`（镜像已内置 tzdata 包，改完重建或直接加 `-e TZ=Asia/Shanghai`） |
| 端口被占用 | 改 `ports` 左侧为本机空闲端口，例如 `"9000:8765"` |
| 本机直接双击 HTML | 会进入降级直连模式：能拿到 K 线与报价，但没有上海金与当日精确开高低 |

---

## 说明

- 服务为单进程 `ThreadingHTTPServer`，无数据库、无状态、不落盘，任意重启无副作用，镜像体积极小（约 60 MB）。
- 容器内以非 root 用户（`gold`, uid 10001）运行。
- 本页数据来自公开免费接口，可能存在延迟或中断，**仅供参考学习，不构成投资建议**。
