# 万能搜索（AI Search Agent）

一个跑在自己电脑上的 AI 搜索助手：用中文提问，它去国内外多个来源检索、打开网页读正文，再用中文把答案讲清楚，并标注每条信息来源。

> 专为"国内网络环境"设计：在没有代理的情况下，也能查到相当一部分国外内容；一旦配了代理，能力自动拉满。

## 特性

- **多引擎并发检索**：必应中文、360、搜狗、中文维基、OpenAlex 学术库、Hacker News、6 家外媒 RSS
- **真正读正文**：不只给搜索摘要，会用 trafilatura 抽取页面正文再交给模型
- **被墙站点兜底**：直连失败时自动改用云端阅读服务，能读到英文维基、外媒文章等
- **答案带引用**：正文里的 `[1][2]` 可点击，直接跳到原始网页
- **诚实的降级**：读不到的页面明确标注"仅摘要"，资料不足时直接说"查不到"
- **本地优先**：数据只存在本机 SQLite，密钥不落仓库
- **开箱即用**：自带启动脚本、桌面图标、开机自启

## 快速开始

需要：Linux / macOS，Python 3.10+，能访问 `api.deepseek.com`。

```bash
git clone <你的仓库地址> wanneng-search
cd wanneng-search
./install.sh      # 建独立虚拟环境 + 装依赖（走国内镜像，不需要 sudo）
./start.sh        # 后台启动并打开浏览器
```

浏览器会打开 <http://127.0.0.1:8899>。

**Windows 用户**：装好 Python 后，在 PowerShell 里：

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8899
```

### 配置 API Key

程序会按以下顺序找 DeepSeek 的 Key，找到即用：

1. 环境变量 `DEEPSEEK_API_KEY`
2. 项目根目录 `.env` 里的 `DEEPSEEK_API_KEY=sk-...`
3. `~/.dsh/.credentials.yaml`（DeepSeek Harness 用户可自动复用）

没有 Key 时页面右上角「自检」会显示连接异常。

## 使用

| 模式 | 说明 |
|---|---|
| **搜资料** | 问问题，全网找答案 |
| **读链接** | 粘贴任意网页地址，读完总结／翻译 |
| **深度模式** | 多读几篇网页，答案更全，速度更慢 |

- 回车发送，Shift+回车换行
- 答案下方「来源」里：绿色 = 已读全文，黄色 = 仅摘要
- 点 `[1]` 直接打开对应网页

## 能力边界

| 场景 | 支持情况 |
|---|---|
| 国内资讯、政策、公司、生活服务 | ✅ 好 |
| 国外技术、产品、论文 | ✅ 好（HN / RSS / OpenAlex / arXiv 可直连） |
| 被墙的单篇网页 | ✅ 能读，但走云端阅读服务，**约 20 次/天** |
| 需要登录的内容（知乎回答、公众号正文、付费墙） | ❌ 读不到全文 |
| 没有代理时的国外网页搜索 | ⚠️ 依赖必应中文的结果覆盖，不如 Google 全 |

**配了代理会更好**：在 `.env` 里填 `SEARCH_AGENT_PROXY=http://127.0.0.1:7890`，程序会自动走代理，国外站点直连，云端额度压力也会小很多。

## 工作原理

```
用户提问
   │
   ├─ ① 规划：让模型把问题拆成 2~3 条检索词（中文 + 英文）
   │
   ├─ ② 检索（并发）
   │     必应中文 · 360 · 搜狗 · 中文维基 · OpenAlex · Hacker News · 外媒 RSS
   │
   ├─ ③ 合并去重排序（域名权重 + 品牌匹配 + 多引擎共识 + 广告/词典/视频站降权）
   │
   ├─ ④ 精读正文（直连 → 云端兜底 → trafilatura 抽正文，SQLite 缓存）
   │
   └─ ⑤ 生成答案（DeepSeek 流式输出，强制标注引用，资料不足就直说）
```

代码结构：

```
app/
├── config.py     配置、密钥、代理自动探测
├── llm.py        DeepSeek 调用与检索词规划
├── search.py     各检索源的抓取与解析
├── fetch.py      网页抓取、正文抽取、反爬识别
├── cache.py      SQLite 缓存与额度计数
├── research.py   流程编排
└── main.py       FastAPI 服务与 SSE 流式接口
web/              原生 JS 前端（无构建步骤）
```

## 常见问题

**页面打不开？**
服务没启动，执行 `./start.sh`。日志在 `data/server.log`。

**提示"今天的云端阅读额度快用完了"？**
读被墙网页用的免费服务每天约 20 次，次日自动重置。可在 <http://127.0.0.1:8899/api/health> 查看当天用量。

**答案说"资料不足"？**
换更具体的说法，或开深度模式。有些内容需要登录才能看，确实读不到。

**换端口？**
`.env` 里设 `SEARCH_AGENT_PORT=8900`。

**换模型？**
`.env` 里设 `DEEPSEEK_MODEL=deepseek-v4-pro`。

## 依赖与致谢

Python 3.10 / FastAPI / httpx / trafilatura / BeautifulSoup，前端原生 JS，Markdown 渲染使用 [marked](https://github.com/markedjs/marked)（MIT）。

检索质量高度依赖各搜索源的可访问性，若某个源失效，在 `app/search.py` 里调整即可。

## License

[MIT](LICENSE)
