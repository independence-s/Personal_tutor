# StudyFlow：第一次对话

FastAPI + OpenAI 兼容模型接口 + MySQL 持久化。聊天历史保存在数据库中，重启后可继续原会话。

## 当前本地环境

- WSL Ubuntu 22.04，MySQL **8.4.13 LTS**。
- MySQL 监听 `127.0.0.1:3307`，X Plugin 已关闭，避免和 Windows MySQL 的 3306/33060 冲突。
- 数据默认在 `/var/lib/mysql`，配置在 `/etc/mysql`，不在项目目录内。其他项目可以共用此 MySQL 服务，但应各自建库、创建专用账号。
- 本机管理员：`sudo mysql --protocol=socket`。root 使用 `auth_socket`，不是应用账号，不使用空密码 TCP 登录。

## 启动

```bash
cd /home/sun/projects/studyflow-first-chat
uv sync
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000/docs> 使用接口。检查服务：

```bash
curl http://127.0.0.1:8000/health
```

`database_connected: true` 表示数据库查询成功；`llm_configured` 仅说明模型配置存在，不保证密钥/额度有效。

项目根目录 `.env` 保留 LLM 配置，并增加了以下数据库配置（示例见 `.env.example`）：

```dotenv
DB_HOST=127.0.0.1
DB_PORT=3307
DB_NAME=studyflow
DB_USER=studyflow_app
DB_PASSWORD=本地随机生成的密码
```

`.env` 仅本机用户可读写，已被 Git 忽略。不要上传、分享或使用 root 替代应用账号。修改配置后重启后端。数据库配置缺失、连接失败或未建表时，后端启动失败，不回退到内存。

## 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 数据库连通性、模型配置状态 |
| POST | `/sessions` | 创建会话，返回 `session_id` |
| POST | `/sessions/{session_id}/messages` | 请求体 `{"message":"什么是词法分析？"}` |
| GET | `/sessions/{session_id}/messages` | 按保存顺序返回历史、`message_count`、`busy` |

每个会话最多 20 轮。模型调用失败或回复为空时不保存消息；成功后用户消息与模型回复在同一事务内提交。MySQL 命名锁确保同一会话在多个后端 worker 间也不能同时生成回复；冲突返回 409。

连接池最多 10 个连接，回复期间占用一个连接持有命名锁，但不保持数据库事务。池等待超过 5 秒返回 503。本项目是本地演示，尚无用户认证，不要将 API 或数据库直接开放公网。请求已提交但网络断开时，不保证客户端重试的幂等性，应先查询历史。

## 新环境首次安装（当前机器已经完成，无需重跑）

仅适用于 WSL Ubuntu 22.04 的全新 MySQL 安装：

```bash
bash scripts/setup-mysql-wsl.sh
uv sync
uv run python scripts/setup_database.py
```

软件源菜单必须选择 `mysql-8.4-lts`。安装向导使用官方 APT 源并核对候选与实际版本，不会改动 Windows MySQL。遇到已有数据目录时会停止，**不要删除数据目录来绕过检查**。

`setup_database.py` 为当前本地实例创建 `studyflow`、两张表和 `studyflow_app@localhost`，只授予这两张表 `SELECT`、`INSERT` 权限。随机密码写入 `.env`，不会输出；原 LLM 配置保留。脚本还会把 root 切换到本机 socket 认证。重复运行可能要求输入 Linux `sudo` 密码；不会自动重置已有应用账号密码或撤销额外权限。`schema.sql` 是可供管理员检查的建库建表定义，不需要在每次启动时执行。

若配置中误写入 `EOF` 导致本次安装同类故障，可使用 `bash scripts/repair-mysql-wsl.sh`；它会备份并恢复为本机 3307 的配置，不删除数据。**该脚本会覆盖 `studyflow-local.cnf` 中的自定义配置，不用于日常启动或其他实例。**

## 测试

```bash
uv run pytest -q
# 显式允许访问 .env 指向的真实数据库；模型始终为模拟，不产生模型费用：
STUDYFLOW_TEST_MYSQL=1 uv run pytest -q -s
```

覆盖输入校验、消息顺序、失败不保存、事务回滚、并发/跨连接池互斥、取消释放锁、20 轮限制、数据库错误处理，以及另一个 Python 进程重新启动应用后读取历史。

真实数据库测试会创建少量独立 UUID 会话。测试输出会给出 `/tmp/studyflow-test-cleanup-*.sql` 路径，包含仅针对本次测试 ID 的删除语句；应用账号无 DELETE 权限，管理员可检查后执行：

```bash
sudo mysql < /tmp/输出中指定的清理文件.sql
```

清理不是服务运行的前提；勿用通配符清理生产会话。日常管理/备份请使用管理员账号，后端不需要建库、建表、删除或修改权限。

官方安装说明：<https://dev.mysql.com/doc/mysql-apt-repo-quick-guide/en/>。
