# 心伴 · AI 智能伴侣

基于 Python、Streamlit、DeepSeek API 和 SQLite 的个人 AI 陪伴应用，支持多轮聊天、历史对话切换、邀请码注册与账号数据隔离。
<img width="2802" height="1606" alt="image" src="https://github.com/user-attachments/assets/888eed7f-21ca-4e96-b8cf-730e1b9f5c62" />

在线体验：待部署后填写。源码：当前仓库。
演示截图：待添加（请使用虚构聊天内容，遮挡账号、邀请码及个人信息）。

## 已实现

- Streamlit 三栏聊天页面、伴侣昵称与交流风格设置。
- DeepSeek 对话接口，最近约 10 轮上下文，超时及失败提示。
- SQLite 保存会话和消息，重启后按账号恢复历史。
- 邀请码注册与登录；随机盐和 PBKDF2-SHA256 密码哈希。
- 数据库层检查会话归属，防止跨账号读取、修改历史。
- 每账号每天 20 次请求，北京时间零点重置；请求前事务扣次，并限制请求间隔。
- 心情打卡页面（此功能暂仅在当前登录会话内保存）。

## 本地运行

建议使用 Python 3.11 或更新的兼容版本。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

设置服务端环境变量 `DEEPSEEK_API_KEY` 和 `APP_INVITE_CODE`，或参考 `.streamlit/secrets.toml.example`，创建不提交 Git 的 `.streamlit/secrets.toml`。

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

向管理员获取邀请码，注册独立账号后体验。无需安装 SQLite 服务，数据库会自动创建为 `data/companion.db`。

## 文件

| 文件 | 用途 |
| --- | --- |
| `app.py` | 页面、登录入口和模型调用 |
| `account_store.py` | 账号存储、密码哈希、会话归属和额度 |
| `requirements.txt` | 依赖 |
| `.streamlit/config.toml` | 页面主题 |
| `.streamlit/secrets.toml.example` | 配置示例，不含真实密钥 |
| `tests/test_accounts.py` | 账号、数据隔离、旧表升级和并发额度测试 |

## 验证

```powershell
python -m unittest discover -s tests -v
```

目前包含 6 项自动测试；这些测试不请求真实 DeepSeek API，也不替代浏览器和公网环境验证。

手动验收：用两个独立账号聊天，确认历史互不展示；退出再登录确认能恢复；新建对话不重置每日额度。

## 部署与数据说明

GitHub 保存源码；Streamlit 需要在运行 Python 的托管环境中启动。GitHub Pages 不运行此应用。

短期演示可从 GitHub 部署到 Streamlit Community Cloud，在应用的 Secrets 中配置密钥和邀请码。
Community Cloud 不保证本地文件持久保存；因此此版本在该平台的 SQLite 账号、聊天和额度均可能丢失。
需要长期保留数据时，使用带持久磁盘的单实例服务器部署此版本，或改造为外部数据库。

- API 费用由服务端密钥所有者承担，次数限制不等于金额预算；控制邀请码传播。
- 聊天会保存到数据库并发送给 DeepSeek。管理员能访问数据库中的聊天；请勿输入敏感信息。
- 密码以哈希保存，聊天内容目前未加密保存。试用版尚无自助密码找回。
- 不提交 API Key、真实邀请码、`.env`、`secrets.toml`、数据库和用户聊天截图。
- 公网入口使用 HTTPS。此实现适合小范围邀请试用，不宣称已具备大规模生产服务能力。

参考：[Streamlit 部署文档](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)、[本地存储限制](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data)。
