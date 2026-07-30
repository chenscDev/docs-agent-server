# 演示环境完整部署指南（服务端 + Android CDN）

> 适用：Ubuntu 22.04 · 2核2G · **仅 Android** · 备案前用公网 IP 自用演示。  
> 当前演示机：`PUBLIC_IP=47.93.207.70`（下文可直接替换；勿把 Token / LLM Key 写进 Git）。

相关文件：

| 路径 | 说明 |
|------|------|
| `docs-agent-server/deploy/nginx.conf` | Nginx：API + `/cdn/` |
| `docs-agent-server/deploy/docs-agent.service` | systemd 模板（演示机已改为 `User=root`） |
| `rn-biz-0.86/project/config/upload.demo.example.json` | 上传配置样例 |
| `rn-biz-0.86/project/config/upload.local.json` | 本机真实配置（已 gitignore） |
| `rn-biz-0.86/project/config/channels/agent-docx/bundles.local.json` | 分包索引（含 CDN url） |

---

## 1. 整体架构

```text
┌──────────────── Android 真机 ────────────────┐
│  rn-dynamic 宿主 APK                         │
│   ① 读 channel=agent-docx 配置               │
│   ② HTTP 拉 common + docs-agent bundle       │
│   ③ docs-agent Settings：Host/Port/Token     │
└───────────────┬───────────────┬──────────────┘
                │               │
                │ /cdn/...      │ /v1/...  /health
                ▼               ▼
        ┌───────────────────────────────────┐
        │  公网 IP :80  Nginx               │
        │   /cdn/  → /var/www/rn-cdn/       │
        │   /      → 127.0.0.1:8000 uvicorn │
        └───────────────────────────────────┘
                        │
                        ▼
              docs-agent-server
              (/opt/docs-agent-server)
              SQLite + FAISS + data/
```

**不要**在 2G 服务器上跑 Metro / `yarn pack:build` / Android 编译。

---

## 2. 服务端部署（已完成可复现）

### 2.1 云控制台

- 安全组入站：`22`、`80`（不要对公网开 `8000`）
- 出站：允许 HTTPS（DashScope）
- SSH：阿里云常用 `root` + `.pem`（本机示例：`Downloads/first-pass.pem`）

```bash
ssh -i /path/to/first-pass.pem -o IdentitiesOnly=yes root@PUBLIC_IP
```

### 2.2 系统准备

```bash
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y nginx python3.10-venv python3-pip build-essential curl rsync git

# 2G 内存建议开 swap
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab

mkdir -p /opt/docs-agent-server /var/www/rn-cdn
```

### 2.3 同步后端代码与密钥

在**开发机**执行（路径按本机调整）：

```bash
PEM=~/Downloads/first-pass.pem
HOST=root@PUBLIC_IP
LOCAL=~/Documents/mine/docs-agent-server

rsync -az --delete \
  -e "ssh -i $PEM -o IdentitiesOnly=yes" \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude 'data/' \
  --exclude 'eval/results/' \
  --exclude '.git/' \
  "$LOCAL/" "$HOST:/opt/docs-agent-server/"

# 含 LLM_API_KEY / API_TOKEN，勿回显、勿提交仓库
scp -i "$PEM" -o IdentitiesOnly=yes "$LOCAL/.env" "$HOST:/opt/docs-agent-server/.env"
```

`.env` 至少包含：

```bash
LLM_API_KEY=...
API_TOKEN=...          # 与 App Settings 一致
# 其它保持 .env.example 默认即可
```

### 2.4 Python 依赖 + systemd

```bash
ssh -i $PEM -o IdentitiesOnly=yes $HOST bash <<'EOF'
cd /opt/docs-agent-server
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt

cat >/etc/systemd/system/docs-agent.service <<'UNIT'
[Unit]
Description=docs-agent-server (FastAPI)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/docs-agent-server
Environment=PATH=/opt/docs-agent-server/.venv/bin:/usr/bin
EnvironmentFile=/opt/docs-agent-server/.env
ExecStart=/opt/docs-agent-server/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5
MemoryMax=1500M

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now docs-agent
systemctl status docs-agent --no-pager
EOF
```

说明：仓库内 `deploy/docs-agent.service` 默认 `User=ubuntu`；阿里云 root 镜像请用上面的 `User=root`。

### 2.5 Nginx

```bash
ssh -i $PEM -o IdentitiesOnly=yes $HOST bash <<'EOF'
cp /opt/docs-agent-server/deploy/nginx.conf /etc/nginx/sites-available/docs-agent
ln -sf /etc/nginx/sites-available/docs-agent /etc/nginx/sites-enabled/docs-agent
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
EOF
```

要点（见 `deploy/nginx.conf`）：

- `location /cdn/` → `alias /var/www/rn-cdn/;`
- `location /` → `proxy_pass http://127.0.0.1:8000;`
- **SSE**：`proxy_buffering off;`，加长 `proxy_read_timeout`

### 2.6 服务端验收

```bash
curl -sS http://PUBLIC_IP/health
# → {"status":"ok"}

curl -sS http://PUBLIC_IP/v1/meta
# → 401 AUTH_REQUIRED（无 Token，正常）

curl -sS -H "Authorization: Bearer <API_TOKEN>" http://PUBLIC_IP/v1/meta
# → 含 llmModel / embeddingModel 的 JSON
```

常用运维：

```bash
journalctl -u docs-agent -f
systemctl restart docs-agent
# 更新代码后再：pip install -r requirements.txt && systemctl restart docs-agent
```

---

## 3. RN CDN 打包与发布（Android）

### 3.1 流程总览

```text
开发机 rn-biz-0.86
  ① 配置 upload.local.json（baseUrl + sync.enabled=true）
  ② yarn pack:publish:common  --platform android --channel agent-docx
  ③ yarn pack:publish docs-agent --platform android --channel agent-docx
       ├─ 产出 project/dist/bundles/...
       ├─ 复制到 project/dist/cdn-local/rn/0.86.0/...
       ├─ 更新 channels/agent-docx/bundles.local.json 的 url
       └─ 自动 rsync → 服务器 /var/www/rn-cdn/（sync.enabled=true）
  ④ 宿主使用该 channel 拉包（无需再手工 rsync）
```

### 3.2 上传配置

```bash
cd rn-biz-0.86
cp project/config/upload.demo.example.json project/config/upload.local.json
```

编辑 `upload.local.json`（务必打开 `sync`，否则 App 仍读旧包）：

```json
{
  "provider": "local",
  "baseUrl": "http://47.93.207.70/cdn",
  "targetDir": "project/dist/cdn-local",
  "pathTemplate": "rn/{rnVersion}/{channel}/{key}/{platform}/{fileName}",
  "keepHistory": 5,
  "defaultChannel": "agent-docx",
  "sync": {
    "enabled": true,
    "host": "root@47.93.207.70",
    "remoteDir": "/var/www/rn-cdn/",
    "sshIdentityFile": "~/Downloads/first-pass.pem"
  }
}
```

`upload.local.json` 已在 `.gitignore`，避免把公网 IP 提交进仓库。

### 3.3 本机构建（Node 按 `.nvmrc`）

```bash
cd rn-biz-0.86
nvm use
yarn install

yarn pack:list
# 确认有 common、docs-agent

yarn pack:publish:common --platform android --channel agent-docx
yarn pack:publish docs-agent --platform android --channel agent-docx
# 日志应出现：[rn-pack] ✓ 远端 CDN 已同步
```

当前演示产物示例（hash 随构建变化）：

| 包 | 公网 URL |
|----|----------|
| common | `http://47.93.207.70/cdn/rn/0.86.0/agent-docx/common/android/common.android.98bf41643377.bundle` |
| docs-agent | `http://47.93.207.70/cdn/rn/0.86.0/agent-docx/docs-agent/android/docs-agent.android.2ab201c27a6b.bundle` |

配置落点：`project/config/channels/agent-docx/bundles.local.json`（`url` 已是上述 HTTP 地址）。

### 3.4 同步到服务器

`sync.enabled=true` 时，`pack:publish` / `pack:upload` 结束会自动 rsync。仅需补同步时：

```bash
yarn pack:sync --channel agent-docx
```

或手工：

```bash
PEM=~/Downloads/first-pass.pem
HOST=root@PUBLIC_IP

rsync -avz --delete \
  -e "ssh -i $PEM -o IdentitiesOnly=yes" \
  project/dist/cdn-local/ \
  $HOST:/var/www/rn-cdn/
```

验收：

```bash
curl -sS -o /dev/null -w '%{http_code} %{size_download}\n' \
  http://PUBLIC_IP/cdn/rn/0.86.0/agent-docx/common/android/common.android.<hash>.bundle
# 期望 200，size > 0

curl -sS -o /dev/null -w '%{http_code} %{size_download}\n' \
  http://PUBLIC_IP/cdn/rn/0.86.0/agent-docx/docs-agent/android/docs-agent.android.<hash>.bundle
```

访问 `/cdn/` 目录本身可能 **403**（关闭了 autoindex），属正常。

---

## 4. 宿主（rn-dynamic）与 App 联调

### 4.1 动态分包

1. Channel 使用 **`agent-docx`**
2. 使用更新后的 `bundles.local.json`（内含 `http://PUBLIC_IP/cdn/...`）
   - 按你们现有机制：打进壳 assets、或远程下发配置指针
3. **不要**再走电脑 Metro 调试入口拉业务包（否则仍依赖局域网）
4. Android 需允许明文 HTTP（演示期）；备案后换 HTTPS

### 4.2 docs-agent API

进入 docs-agent → Settings：

| 字段 | 值 |
|------|-----|
| Host | `47.93.207.70`（或你的 PUBLIC_IP） |
| Port | **`80`**（Nginx；不是 8000） |
| Token | 与服务器 `.env` 的 `API_TOKEN` 一致 |

`src/docs-agent/config.ts`：

- 本地开发：`DEV_API_PORT=8000`
- 云演示：Settings 改 Port=80，或改 `DEV_API_PORT=80` / `DEV_API_HOST`

### 4.3 端到端验收清单

- [ ] `curl http://PUBLIC_IP/health` → ok  
- [ ] 带 Token 的 `/v1/meta` → 200  
- [ ] common / docs-agent bundle → HTTP 200  
- [ ] 宿主能打开 docs-agent（不依赖 Metro）  
- [ ] 上传 md/pdf → ready  
- [ ] 提问有流式回答与引用  

---

## 5. 日常更新流程

### 只改后端

```bash
rsync ... docs-agent-server/ → /opt/docs-agent-server/
ssh ... 'cd /opt/docs-agent-server && .venv/bin/pip install -r requirements.txt && systemctl restart docs-agent'
```

### 只改 RN 业务 UI

```bash
yarn pack:publish docs-agent --platform android --channel agent-docx
# 若改了 common 依赖再 publish:common
# sync.enabled=true 时会自动 rsync；日志应有「远端 CDN 已同步」
# 手机杀进程重开；必要时清分包缓存
```

### 改 API 契约时

先发后端 → 再打 RN 包 → 再测，避免旧包打新接口。

---

## 6. 目录与端口速查

| 位置 | 路径 / 端口 |
|------|-------------|
| 后端代码 | `/opt/docs-agent-server` |
| 后端数据 | `/opt/docs-agent-server/data/`（首次运行自动建） |
| CDN 根目录 | `/var/www/rn-cdn/` ↔ URL `/cdn/` |
| uvicorn | `127.0.0.1:8000`（仅本机） |
| 公网 | `:80` Nginx |
| systemd | `docs-agent.service` |

CDN 对象路径模板：

```text
/var/www/rn-cdn/rn/{rnVersion}/{channel}/{key}/{platform}/{fileName}
URL: http://PUBLIC_IP/cdn/rn/{rnVersion}/{channel}/{key}/{platform}/{fileName}
```

---

## 7. 排障

| 现象 | 排查 |
|------|------|
| `/health` 通但 App 连不上 | Port 是否 80；Token 是否一致；手机网络是否可达公网 IP |
| 401 | `.env` 的 `API_TOKEN` 与 Settings |
| 拉包失败 | `curl -I` 对应 bundle；安全组 80；`bundles.local.json` url 是否仍是旧 hash |
| 一直 Metro | 宿主是否仍填局域网调试 Host；应走 channel CDN 配置 |
| OOM / 解析卡死 | `free -h`、swap；单 workers；减小上传 PDF |
| SSE 无增量 | Nginx 是否 `proxy_buffering off` |
| `fitz` / pymupdf | 服务必须用 `/opt/.../.venv`（systemd ExecStart 已指定） |

---

## 8. 备案后迁移

1. 域名 A 记录 → Nginx `server_name`  
2. certbot 上 HTTPS  
3. `upload.local.json` 的 `baseUrl` 改为 `https://域名/cdn`  
4. 重新 `pack:publish` + rsync（或只改配置里的 url 前缀并保证文件仍在）  
5. App Host 改为域名，Port `443`（或 HTTPS 默认端口逻辑）  
6. 逐步关掉明文 HTTP  

---

## 9. 本次演示机已落地状态（2026-07-24）

| 项 | 状态 |
|----|------|
| API | `http://47.93.207.70/health` ok；`/v1/*` 需 Bearer |
| CDN common | `.../common.android.98bf41643377.bundle` → 200 |
| CDN docs-agent | `.../docs-agent.android.2ab201c27a6b.bundle` → 200 |
| 服务进程 | `systemctl status docs-agent` active |
| 下一步（你本地） | 宿主载入 `agent-docx` 配置 → Settings Host/Port/Token → 真机走通 |

更短的分册：`DEPLOY_DEMO.md`（偏服务端）、业务仓 `project/docs/DEPLOY_ANDROID_CDN.md`（偏打包）；**以本文为完整主文档**。
