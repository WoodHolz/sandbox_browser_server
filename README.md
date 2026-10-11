# sandbox-yuan

给同事用的浏览器沙盒。容器镜像和端口约定直接沿用 infomator（ai-manus）sandbox：Chrome CDP `9222`，沙盒 API `8081`，VNC `5901`。

```
POST /api/v1/sandboxes
        -> 启动 infomator-sandbox，等到 CDP /json/version
        -> 返回 browser_id + cdp_url

用法 1 — 同事本机 Playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        ...
        POST .../heartbeat          # 长时间占用时重置倒计时
        POST .../events/script-finished 或 DELETE
        -> 空闲宽限后销毁容器

用法 2 — 本服务代跑脚本（可多次，每次一段）:
        POST .../run  {"script": "..."}   # 进程退出后沙盒仍在，回到 ready
        POST .../run  {"script": "..."}   # 下一段
        POST .../heartbeat                # 与用法 1 相同
        POST .../events/script-finished 或 DELETE
```

## 生命周期

| 状态 | 含义 |
| --- | --- |
| ready | CDP 可用，等待连接或下一次 `/run` |
| running | `/run` 执行中（此期间 sweeper 不拆容器） |
| finished | 显式 `script-finished`，`IDLE_GRACE_SECONDS` 后销毁 |
| destroyed | 容器已删 |

`/run` 结束**不会**自动销毁。未心跳且未显式结束时，`MAX_LIFETIME_SECONDS` 到期且不在 running 时销毁。也可 `DELETE /api/v1/sandboxes/{browser_id}`。

API 状态只在进程内存里。`run.py` 重启后 `list` 会空，但 Docker 里可能仍有本服务创建的容器。处理方式：

- 孤儿清理**只认** Docker label `app=sandbox-yuan`，并且名称必须以 `SANDBOX_NAME_PREFIX-`（默认 `yuan-sandbox-`）开头
- **不会**按镜像名删除，因此同机上的 infomator（常见名 `sandbox-<id>`、无此 label）不会被误删
- 启动 / 停止脚本与进程 lifespan、sweeper 都走上述规则
- 容器 TTL 仍是兜底；`SANDBOX_NAME_PREFIX` 不要改成过于泛化的 `sandbox`

用法 1 / 用法 2 长时间占用时，同事定时调用心跳重置回收倒计时：

```bash
curl -X POST "$SANDBOX_BASE/api/v1/sandboxes/$BROWSER_ID/heartbeat"
```

心跳是**倒计时重置**：每次调用把回收倒计时重新设为约 `HEARTBEAT_EXTEND_SECONDS`（默认 1800 秒 = 30 分钟），响应里看 `remain_seconds`。不超过 `created_at + ABSOLUTE_MAX_LIFETIME_SECONDS`（默认 24 小时）。建议轮询间隔短于倒计时窗口（例如每 10～15 分钟）。全部用完仍应调 `script-finished` 或 `DELETE`。容器内 `SANDBOX_TTL_MINUTES` 需单独设够（例如 1440），心跳改不了容器内部超时。

创建后若长时间不心跳，仍按 `MAX_LIFETIME_SECONDS`（默认 30 分钟）到期回收。

## 运行

```bash
cp .env.example .env   # 默认 SANDBOX_IMAGE=woodholz/infomator-sandbox:latest
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 run.py                 # 等价于 uvicorn app.main:app --host 0.0.0.0 --port 8090
# python3 run.py --reload      # 开发热重载
```

HOST / PORT 可读 `.env`。同事若自己连 CDP，只需要本服务和已构建的沙盒镜像，不必在调用方安装 Chromium。

### systemd

仓库里有 `deploy/sandbox-browser-server.service`（默认路径 `/home/ubuntu/sandbox_browser_server`）。

```bash
sudo cp deploy/sandbox-browser-server.service /etc/systemd/system/
# 若部署目录或用户不同，先改 Unit 里的 User / WorkingDirectory / ExecStart
sudo systemctl daemon-reload
sudo systemctl enable --now sandbox-browser-server
sudo systemctl status sandbox-browser-server
```

日志：`journalctl -u sandbox-browser-server -f`

开发阶段不用 systemd 时，用仓库脚本区分多个 `run.py` 服务：

```bash
chmod +x deploy/start-background.sh deploy/stop-background.sh
./deploy/start-background.sh
./deploy/stop-background.sh   # 只停本仓库，不会 pkill 其它项目的 run.py
```

不要用全局 `pkill -f "python run.py"`。若手启，请用完整路径区分，例如：
`pkill -f "/home/ubuntu/sandbox_browser_server/run.py"`

### nginx 反代（不对外暴露后端端口）

仓库里有这些 conf，挂到 `dev.gameday.tech` 的 HTTPS `server` 里：

| conf | 公网路径 | 本机后端 |
| --- | --- | --- |
| `deploy/nginx-sandbox-browser-server.conf` | `/sandbox` | `127.0.0.1:8090` |
| `deploy/nginx-research.conf` | `/research` | `127.0.0.1:8010` |
| `deploy/nginx-page.conf` | `/page` | `127.0.0.1:5052` |

同事只用：

```text
https://dev.gameday.tech/sandbox
https://dev.gameday.tech/research
https://dev.gameday.tech/page
```

不必再写 `:8090` / `:8010` / `:5052`。本机 / 同事：

```bash
export SANDBOX_BASE=https://dev.gameday.tech/sandbox
curl -s "$SANDBOX_BASE/docs" -o /dev/null -w '%{http_code}\n'
python3 examples/bilibili.py usage2
```

反代生效后，服务只听本机（`.env`）：

```bash
HOST=127.0.0.1
PORT=8090   # sandbox；research 用 8010；page 用 5052
```

并确认防火墙 / 安全组不再放行公网 8090 / 8010 / 5052。`usage1` 的 CDP 仍是容器映射端口，反代管的是 HTTP API，不是 CDP。

## 浏览器状态

与 infomator 交接顺序相同，接口是 `POST /api/v1/sandboxes/{browser_id}/context`：

1. `cookies`：`context.add_cookies`，在打开页面前写入。
2. `url`：导航到目标页。
3. `localStorage` / `sessionStorage`：在该页面上 `setItem`。

只上传 Cookie 时仍可用 `POST /api/v1/sandboxes/{browser_id}/cookies`。有 storage 时必须带 `url`。

## 示例

`examples/bilibili.py` 走完整流程：创建沙盒、打印状态、用 `examples/bilibili_cookies.json` 调用 `/context`、再打印状态，然后打开 Bilibili。

```bash
# 用法 1：同事本机 Playwright 连接 cdp_url，结束后通知 script-finished
python3 examples/bilibili.py usage1

# 用法 2：把脚本交给 POST /run（可多次），示例末尾显式 DELETE
python3 examples/bilibili.py usage2
```

激活 `.venv` 后也可以直接用 `python`（venv 里会有这个命令）。

打开 `https://research.ventify.ai/auto.html`（与 Bilibili 示例独立，默认不注入 Cookie）：

```bash
export SANDBOX_BASE=https://dev.gameday.tech/sandbox
python3 examples/ventify_auto.py usage2
# 开发服务器本机也可：python3 examples/ventify_auto.py usage1
```

完整重建状态（cookies + localStorage + sessionStorage，服务端会回读校验）：

```bash
export SANDBOX_BASE=https://dev.gameday.tech/sandbox
python3 examples/context_demo.py usage2
# 状态文件：examples/context_demo_state.json
```

`/context` 若返回 503，多半是页面 `goto` 失败（以前默认等 `load` 易超时）；当前实现已改为 `domcontentloaded` + 120s。看服务日志 `logs/run.log` 里的 `context apply failed`。
