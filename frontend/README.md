# StudyFlow 引导式练习前端

需要 Node.js 22.12+（已使用 Node.js 22 验证）。

```sh
npm install
npm run dev
npm test
npm run build
```

开发服务器将 `/api` 请求代理到 `http://127.0.0.1:8001`，并去掉 `/api` 前缀。确保 FastAPI 后端已启动且配置了模型。

可通过环境变量或前端目录中的 `.env.local` 覆盖目标地址：

```sh
API_TARGET=http://127.0.0.1:8000 npm run dev
```

`npm run preview` 也使用同样的代理配置。生产部署需自行配置 `/api` 的反向代理。

练习状态只保存在当前页面内，刷新后需重新开始。自动化测试使用模拟 API 响应，覆盖重试、补充、变式、异常与会话过期流程。
