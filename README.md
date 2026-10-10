# sandbox-yuan

给同事用的浏览器沙盒。容器镜像和端口约定直接沿用 infomator（ai-manus）sandbox：Chrome CDP `9222`，沙盒 API `8081`，VNC `5901`。

```
POST /api/v1/sandboxes
        -> 启动 infomator-sandbox，等到 CDP /json/version
        -> 返回 browser_id + cdp_url

同事 Playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        ...
POST /api/v1/sandboxes/{browser_id}/events/script-finished
        -> 空闲宽限后销毁容器

或者由本服务代跑脚本:
POST /api/v1/sandboxes/{browser_id}/run
        {"script": "... python, 变量 cdp_url / browser_id 已注入 ..."}
        进程退出即视为脚本结束事件
```

## 生命周期

| 状态 | 含义 |
| --- | --- |
| ready | CDP 可用，等待连接或 `/run` |
| running | `/run` 执行中（此期间 sweeper 不拆容器） |
| finished | 收到脚本结束事件，`IDLE_GRACE_SECONDS` 后销毁 |
| destroyed | 容器已删 |

未收到结束事件时，`MAX_LIFETIME_SECONDS` 到期且不在 running 时销毁。也可 `DELETE /api/v1/sandboxes/{browser_id}`。

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

### nginx 反代（不对外暴露 8090）

仓库里有 `deploy/nginx-sandbox-browser-server.conf`。挂到 `dev.gameday.tech` 的 HTTPS `server` 里后，同事只用：

```text
https://dev.gameday.tech/sandbox
```

不必再写 `:8090`。本机 / 同事：

```bash
export SANDBOX_BASE=https://dev.gameday.tech/sandbox
curl -s "$SANDBOX_BASE/docs" -o /dev/null -w '%{http_code}\n'
python3 examples/bilibili.py usage2
```

反代生效后，服务只听本机（`.env`）：

```bash
HOST=127.0.0.1
PORT=8090
```

并确认防火墙 / 安全组不再放行公网 8090。`usage1` 的 CDP 仍是容器映射端口，反代管的是 HTTP API，不是 CDP。

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

# 用法 2：把脚本交给 POST /run，由本服务执行，进程退出即结束
python3 examples/bilibili.py usage2
```

激活 `.venv` 后也可以直接用 `python`（venv 里会有这个命令）。
