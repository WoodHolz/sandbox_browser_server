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
cp .env.example .env   # SANDBOX_IMAGE 与 infomator 的 sandbox 镜像一致
pip install -r requirements.txt
python -m playwright install chromium   # 仅 /run 代跑脚本时需要本机 Playwright
uvicorn app.main:app --host 0.0.0.0 --port 8090
```

同事若自己连 CDP，只需要本服务和已构建的 `infomator-sandbox` 镜像，不必在调用方安装 Chromium。

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
python examples/bilibili.py usage1

# 用法 2：把脚本交给 POST /run，由本服务执行，进程退出即结束
python examples/bilibili.py usage2
```
