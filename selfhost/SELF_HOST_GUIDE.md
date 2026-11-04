# ClubOS NEW 自托管部署指南

> 背景：公开托管 `*.app.workbuddy.host` 的网关会剥离所有会话载体（Cookie / 自定义请求头 / 查询参数），
> 导致浏览器登录后无法保持会话、立刻被踢回登录页（"闪退"）。因此**强制登录在公开托管链接上无法实现**。
> 自托管到自有域名（HTTPS）后，会话 Cookie 正常传递，"登录 + 多租户不串台"全部正常工作。
> 本项目的后端代码（企业资料、登录门槛、多租户隔离）在自托管环境下**直接可用，前端无需改动**。

---

## 一、前置条件

1. 一台**公网可达的 Linux 服务器**（建议 2C2G 起），开放 80 与 443 端口（Caddy/certbot 自动申请证书需要）。
2. 一个**已备案的域名**（如 `topoutdoor.cn`），其 A 记录指向该服务器公网 IP。
3. 下列二者之一：
   - **方案 A（推荐，最省心）**：服务器已安装 Docker + Docker Compose。
   - **方案 B（裸机）**：服务器有 Python 3.11+（或自建 venv），并安装 nginx + certbot。

> 没有服务器？可先用任意云厂商（阿里云/腾讯云/华为云）开一台按量实例，完成备案域名解析即可。

---

## 二、方案 A：Docker 一键（推荐）

1. 把整个 `clubos-new/` 目录上传到服务器（**无需上传 `venv/`、`static/uploads/`、`static/demo/`、`versions/`、`outputs/`、所有 `*.db`** —— 这些已被 `.dockerignore` 排除）。
2. 进入 `clubos-new/selfhost/`：
   ```bash
   cd clubos-new/selfhost
   cp .env.example .env
   # 编辑 .env：设置 CLUBOS_PLATFORM_ADMIN_PASS（≥14位强密码）、DOMAIN
   nano .env
   ```
3. 启动（Caddy 会自动申请并续期 Let's Encrypt 证书）：
   ```bash
   docker compose up -d
   docker compose logs -f   # 观察启动与证书申请日志
   ```
4. 浏览器访问 `https://你的域名/`，应跳转到登录页；用 `admin` / 你设的密码登录 `/platform`。

> 数据持久化在 Docker 卷 `clubos_data`；升级时只需重新 `docker compose up -d --build`，数据库不丢。

---

## 三、方案 B：裸机（systemd + nginx + certbot）

1. 上传代码到 `/opt/clubos-new/`，安装依赖：
   ```bash
   cd /opt/clubos-new
   python3 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   mkdir -p /opt/clubos-new/data
   ```
2. 配置 `clubos.service`（已提供模板），编辑其中的 `CLUBOS_PLATFORM_ADMIN_PASS` 与路径，然后：
   ```bash
   sudo cp selfhost/clubos.service /etc/systemd/system/clubos.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now clubos
   sudo systemctl status clubos   # 确认 running
   ```
3. 配置 nginx（模板 `selfhost/nginx-clubos.conf`），软链并申请证书：
   ```bash
   sudo cp selfhost/nginx-clubos.conf /etc/nginx/sites-available/clubos
   sudo ln -s /etc/nginx/sites-available/clubos /etc/nginx/sites-enabled/clubos
   sudo nginx -t && sudo systemctl reload nginx
   sudo certbot --nginx -d 你的域名   # 自动 HTTPS + 自动续期
   ```
4. 访问 `https://你的域名/` 登录。

---

## 四、重要说明

### 1. 必须 HTTPS
`CLUBOS_SECURITY_MODE=production` 下登录 Cookie 标记为 `Secure`，**只在 HTTPS 下发送**。若用纯 HTTP，登录会"成功却立刻掉线"。所以无论哪种方案都必须套 HTTPS（Caddy/certbot 已自动处理）。

### 2. 平台管理员账号
- 总后台 `/platform` 用 `CLUBOS_PLATFORM_ADMIN_USER`（默认 `admin`）+ `CLUBOS_PLATFORM_ADMIN_PASS`（默认 `ClubOS@2026Admin!`，建议改成自己的强密码）。
- 该账号在**首次启动**时自动创建；改密码需修改环境变量并重启/重建容器。
- 登录 `/platform` 后，进入各俱乐部「详情 → 重置并发送密码」，把后台登录凭据分发给对应俱乐部（即"忘记密码"重置动作）。

### 3. 多租户隔离（天然生效）
后端 `/api/club/*` 按会话中的 `club_id` 强制隔离，各俱乐部只能看自己的中心，**不会串台**。这是代码层保证，与部署方式无关。

### 4. 现有数据（workbuddy 托管期间的 club1 等）
- 自托管是**全新数据库**，不会自动带 workbuddy 沙箱里的旧数据（club1、测试俱乐部等）。
- 若需要迁移旧数据，需另行提供线上 SQLite 库文件，再做导入；通常内测数据可直接重建，无需迁移。
- 自托管上线后，原 `clubos-backend.app.workbuddy.host` 链接可保留（闪退不可用）或下线，把对外域名切到自有域名即可。

### 5. 企业资料字段（需求 B）
俱乐部详情弹窗已支持：公司名称 / 法人 / 公司地址 / 登录账户（账户号）/ 密码脱敏（默认 `••••••••`，仅"重置并发送密码"时生成新密码）。后台可用 `PATCH /api/platform/clubs/{id}` 补录企业资料。

---

## 五、上线后自检

- [ ] 访问 `https://域名/` 跳登录页，未登录访问 `/platform` 被弹回登录页（强制登录生效）。
- [ ] `admin` 登录 `/platform` 成功，能看到俱乐部列表。
- [ ] 用某俱乐部账号登录 `/club`，确认看不到其他俱乐部数据（不串台）。
- [ ] 俱乐部详情弹窗显示企业资料与脱敏密码，可「编辑资料」「重置并发送密码」。
