# 服务端部署摘要

完整流程（含 RN CDN）见：**[DEPLOY_GUIDE.md](./DEPLOY_GUIDE.md)**。

## 当前演示机速查

| 项 | 值 |
|----|-----|
| IP | `47.93.207.70` |
| 健康检查 | `http://47.93.207.70/health` |
| API | `http://47.93.207.70/v1/...`（需 `Authorization: Bearer`） |
| CDN | `http://47.93.207.70/cdn/rn/0.86.0/agent-docx/...` |
| 代码目录 | `/opt/docs-agent-server` |
| CDN 目录 | `/var/www/rn-cdn` |
| 进程 | `systemctl status docs-agent` |

模板文件：`deploy/nginx.conf`、`deploy/docs-agent.service`。
