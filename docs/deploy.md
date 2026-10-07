# 部署指南（Docker）

把整个应用跑在一台云服务器上，单容器、无外部依赖（除密钥对应的云服务）。

## 部署形态

```
浏览器 ──> http://服务器IP（80 端口）
              │
         Nginx 容器（反向代理：大文件上传限制、超时、gzip、HTTPS 入口）
              │
         FastAPI 容器（内部网络 :8000，不对外）
         ├─ uvicorn --workers 1
         ├─ /api + 托管前端静态文件（同源，无跨域）
         ├─ SQLite + 上传音频 → 挂载卷 ./data
         └─ ffmpeg（imageio-ffmpeg 自带，无需另装）
              │
              ├─ DeepSeek API（复盘五步）
              ├─ 百炼 API（转写）
              └─ 阿里云 OSS（音频中转）
```

两个容器：Nginx 负责对外（大文件上传的体积/超时限制是它存在的最大理由，
Nginx 默认 1MB 请求体会让录音上传直接 413）；FastAPI 只在内部网络，
不直接暴露。配置要求：1 核 1G 起步，磁盘 20G+。

## 首次部署

前提：一台 Ubuntu 云服务器，安全组放行 **80/TCP**（控制台 → 安全组规则里配，
只放行 80，其他一律不开；8000 由 Nginx 内部转发，**不要**对外）。

### 1. 装 Docker

```bash
curl -fsSL https://get.docker.com | sh
```

### 2. 拉代码

仓库是私有的，先在 GitHub 生成一张令牌：
Settings → Developer settings → Fine-grained tokens → 新建，
只授予 `interview-review` 这一个仓库的 **Contents: Read**，有效期建议设 90 天。

```bash
git clone https://<令牌>@github.com/kitecba/interview-review.git
cd interview-review
```

### 3. 配置密钥

```bash
cp .env.example .env
nano .env
```

六项必填（与本地 `.env` 相同，可照抄）：`DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、
`OSS_ENDPOINT`、`OSS_BUCKET`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`。

**外加一项**：`APP_ACCESS_CODE=你自己定的口令`。
**不设就是无鉴权裸奔**——任何扫到端口的人都能上传录音、消耗你的付费额度。

### 4. 构建并启动

```bash
docker compose up -d --build
```

首次构建要几分钟（下载镜像 + npm 编译前端）。

### 5. 验证

```bash
curl http://127.0.0.1:8000/api/health
```

返回 `"ready":true` 后，浏览器打开 `http://服务器IP`（80 端口，不用加端口号）：
会弹出访问口令输入框，填 `APP_ACCESS_CODE` 的值即可进入。

### 6. 排查

```bash
docker compose logs -f app       # 后端日志（启动日志里会明确列出缺哪个配置）
docker compose logs -f nginx     # Nginx 日志（502/413 先看这里）
docker compose ps                # 容器是否在重启循环
```

常见问题：
- **上传返回 413** —— `nginx/default.conf` 里 `client_max_body_size` 不够大
- **打开是 502** —— 后端容器没起来，先看 app 日志
- **改了 Nginx 配置** —— `docker compose restart nginx` 生效

## 日常使用

**备份**——所有数据（数据库、上传记录）都在服务器的 `interview-review/data/`
目录里，备份就是拷这个目录：

```bash
tar czf backup-$(date +%F).tar.gz data/
```

**更新版本**——本地改完推送 GitHub 后：

```bash
git pull && docker compose up -d --build
```

**看实时日志**：`docker compose logs -f app`，Ctrl-C 退出（不影响服务）。

## 局限与后续

- **HTTP 明文**：口令和录音内容都是明文传输。要上 HTTPS 需要一个域名（备案）：
  域名解析到服务器，证书放到 `./nginx/certs/`，按 `nginx/default.conf` 里的
  注释启用 443 块并重启。证书用 Let's Encrypt 免费签（certbot 一条命令）。
- **单实例**：任务队列是进程内状态，容器里固定 `--workers 1`，多副本会坏。
- **容器重建不丢数据**：SQLite 在挂载卷 `./data` 里；但**别删这个目录**。
- **国内服务器**：拉 GitHub 可能需要代理；也可以本地 `docker save` 打包镜像
  传上去 `docker load`（app 和 nginx 两个镜像都要）。
