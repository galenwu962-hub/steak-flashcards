# 交接说明（给下一个工作会话）

用户是 pLeace 必乐时的老板（吴之洋，钉钉手机号 18576420071），不懂编程，全程用中文，解释要简短。
先读本文件、README.md，再动手。所有代码在分支 `claude/restaurant-review-analysis-tool-c8bmky`。

## 1. 先恢复数据（不要重新拉取和重新解析，那要再花约 11 美元）

数据库快照存在一个私有 Artifact 里：https://claude.ai/artifact/EUAPHi53zTujY8RUwRKB6B
用 Artifact 工具 `read`，`path` 为 `reviews-backup.db.gz.b64.txt`，它会存到本地，然后：

```bash
cd review-analytics && pip install -r requirements.txt
base64 -d <保存下来的文件> | gunzip > data/reviews.db
python -m app.analyze status        # 应显示已分析 5,367 条
```

快照时间 2026-09-26 10:30，内容：33,995 条评价（数据到 2026-09-25）、5,367 条 AI 解析（2026-06-28 以来）、
13 条回复建议试写、94 条改善事项与 10 个跨店主题（2026-09-12 至 09-25）。
「顾客说」的登录态没有备份，要拉新数据需重新走验证码登录（见 README 第一步和下文第 5 节）。

## 2. 已经定下来的口径和规则

- 中差评 = AI 判定消极或中性（有文字的以内容为准，不看星级）；未解析的按星级 ≤ 3。门店分档：< 8% 优秀，8%–12% 正常，> 12% 问题。
- AI 解析用 `claude-sonnet-5`，Batch 模式，默认跳过 40 字内的 5 星短评。
- 回复建议（app/reply.py + app/knowledge.py）：店长口吻，像朋友，不卑微；**回复里不解释原因、不用内部术语、不自我诊断**；
  预制菜问题不正面回答，虚心接受；不承诺任何补偿（补救政策待营运确认）；退款和严重投诉引导打门店电话找店长。
- 改善事项（app/actions.py）：按门店把负面意见（含好评里的抱怨）归纳成 3–8 条，带负责角色（店长 / 品控 / 区域经理）、动作、顾客原话；
  食安、虫害、卫生一律单列并标升级。
- 可落地审核（app/actionability.py，用户 2026-09-26 定）：店长拿到事项必须知道改哪里（具体菜、时段、环节、岗位、设备）。
  笼统的（"上菜慢""贵""一般"）先回评价原文找具体信息改写；原文也没有的判「仅知晓」，只让店长留意，不设截止日期、不提醒、不计入完成率。
  食安卫生类不能判仅知晓。每次生成事项后都要跑一遍。
- 「相关评价数」= 事项背后有几条评价（去重），不是原话句数（evidence_count 是句数）。
- 分类与跨店主题（app/themes.py）：9 个固定分类；覆盖 ≥ 3 家店的问题算跨店主题。
- 组织：区域经理杨丽娜、异香（阿香，兼品控）。店长名单待杨丽娜提供。

## 3. 用户最关心的事（按优先级）

1. 每条评价里的意见都要被提炼成门店能执行的改善事项，并形成闭环。
2. 跨门店普遍的问题（单店看不出来的）。
3. 一页纸的每周统一看板给营运和店总：信息聚焦、Less is more。用户已同意的思路：每店只看中差评率、分档、较上周变化；
   全公司只看本周红线事项、新增跨店问题、上周事项处理进度。
   **已做第一版**（2026-09-26）：`python -m app.weekly` → `data/weekly/weekly.html`，发布在 https://claude.ai/artifact/GoPuM4goDVA7raq2fgU4EH
   （用 Artifact 工具以该文件重新发布，带 url 更新同一链接）。一周 = 截至最新评价日的 7 天。事项完成状态（action_items.status）目前没人填，
   进度全是 0；跨店问题"新增"按分类跟上一期主题比，首期全部算新增。
4. 与品控阿香的 QSC 管控表交叉检验：线下发现且线上印证的（最紧急）/ 只有线下 / 只有线上（巡检盲区）。她会定期发云文档表格过来。**尚未做**。
5. 钉钉推送。

## 4. 钉钉接入（用户给的规矩，照做）

- 用钉钉官方 CLI `dws`（已通过环境 Setup script 安装，`dws version` ≥ v1.0.61）。以用户本人身份 OAuth 登录，不用应用凭证。
- 登录必须用设备流：`dws auth login --device --no-browser --profile ding4568e64363f30dc0a1320dcb25e91351`
  （新容器里没有已存 profile，带 `:userId` 的写法会报 profile not found，只写 corpId）
  把输出的短链接和授权码原样发给用户扫；`dws auth status` 看到 `"authenticated": true` 才算成功；先跑只读测试
  `dws contact user search --query "板栗" --format json`。
- token 约 2 小时过期，自动刷新是坏的，过期就重新走设备流。token 不写环境变量、不贴聊天、不用 `dws auth export`。
- 发群消息：`dws chat message send --group "<会话ID>" --title "标题" --text "$(cat 文件)" --format json`；
  @ 人要同时传 `--at-open-dingtalk-ids` 和正文 `<@ID>` 占位符，ID 用 `dws contact user search` 查，不许编；
  发完用 `dws chat message query-send-status --open-task-id <id>` 确认 `success: true`。
- **只有用户说"发"才能往群里发，草稿先给他看。**
- 读群消息：`--time` 用 `yyyy-MM-dd HH:mm:ss`，加 `--page-all` 翻到底，不按关键词过滤。
- 无人值守定时推送才用自定义机器人 webhook（token 存环境变量 `DINGTALK_*_WEBHOOK_TOKEN`），见用户原话第 6 步。
- 改善事项清单已建成钉钉云文档（2026-09-26）：https://alidocs.dingtalk.com/i/nodes/9bN7RYPWdMdve6n7ijR336w9VZd1wyK0
  重新生成：`python -m app.export_items` → `NODE_PATH=$(npm root -g) node scripts/report_docx.js data/reports/items.json data/reports/改善事项清单.docx`
  → `dws doc +import --file data/reports/改善事项清单.docx --folder <我的文件 rootFolderId> --name ...`
  （rootFolderId 用 `dws wiki space list --type mySpace` 查；不带 --folder 会报默认位置不唯一）。

## 5. 环境要点（上一个会话踩过的坑）

- API 密钥在环境变量 `REVIEW_ANALYTICS_API_KEY`，代码会读；不要打印或写进文件。
- Playwright 全局已装，Chromium 在 `PLAYWRIGHT_BROWSERS_PATH`，不要 `playwright install`。
- 外网走 `$HTTPS_PROXY`；浏览器要先导入代理 CA：
  `apt-get update -q && apt-get install -y -q libnss3-tools && mkdir -p ~/.pki/nssdb && certutil -N -d sql:$HOME/.pki/nssdb --empty-password && certutil -A -d sql:$HOME/.pki/nssdb -n ccr-agent-proxy -t "C,," -i /root/.ccr/agent-proxy-ca.crt`
  浏览器 `chromium.launch({ proxy: { server: process.env.HTTPS_PROXY } })`；访问本机服务用 `--no-proxy-server`，curl 加 `--noproxy '*'`。
- 「顾客说」登录：验证码标签 → 点手机号框坐标 (720,342) 输入 → 获取验证码 → 图形验证码截图发给用户认（不要自己认）→ 填入
  placeholder"不区分大小写"的框 → 确认 → 短信验证码由用户发来 → 点第二个"验证码"文字后 keyboard.type → 登录/注册 →
  `ctx.storageState({path:'review-analytics/scripts/state.json'})`。用长驻浏览器进程（HTTP 驱动小服务）等用户回复。
- 后台任务用 run_in_background；等待用 until 循环，不要 sleep 轮询；Batch 用 send_later 每 15 分钟 collect 一次。
- 这台机器的 LibreOffice 不能用（转 PDF 失败），docx 用 python-docx 读回核对。
- `docx` npm 包要全局装并设 `NODE_PATH=$(npm root -g)`。
- 不要把 data/、scripts/data/、state.json 提交到 git。
- 看板在线版 Artifact：https://claude.ai/artifact/LFWBvRSia8h4b7H3DZt7cW（用 `python -m app.export_static` 生成后，
  以 `data/static/index.html` 为页面、`echarts.min.js` 和 `data.js` 为 files 重新发布，files 用绝对路径）。

## 6. 改善事项跟进表（钉钉 AI 表格，2026-09-26 建）

- 表格「门店改善事项跟进」：https://alidocs.dingtalk.com/i/nodes/Amq4vjg890Dmk3B3sxYMD45nJ3kdP0wQ ，94 条事项已导入，
  字段「事项编号」= action_items.id。下发日期统一 9-28；截止日期 = 红线 +1 天、高 +3、中 +7、低 +14。负责人字段暂空，等杨丽娜给店长名单后填。
- 视图：全部事项（按门店）/ 处理看板（按状态，只含「整改」）/ 逾期事项 / 红线事项 / 仅知晓（留意即可）；仪表盘「改善事项进度」6 个图表，
  完成进度、各门店处理情况、未完成分类都只算「整改」。
- 2026-09-26 可落地审核后：57 条原本具体、29 条按评价原文改写、8 条仅知晓（类型=仅知晓，截止日期已清空，所以不会触发提醒）。
  日期清空要传 ""（传 null 无效）；单选字段清不掉，所以仅知晓的状态仍显示待处理，靠「类型」区分。字段：类型 Rxzh58D，说明 WlgwxOy，相关评价数 Jzs930T。
- 自动化（已启用）：到期当天提醒、逾期一天提醒（有负责人发负责人+管理员，没有只发管理员）、每周一逾期汇总（只发管理员）。
  管理员 = 流程创建人（吴之洋）。以后交营运管理时要把流程创建人/接收人换成营运。
- 周报进度：先 `python -m app.sync_dingtalk` 把表格状态拉回数据库，再 `python -m app.weekly`。
- 下一期事项要追加到同一张表（record create，字段 ID 见 app/sync_dingtalk.py 和 scratch 里的写法），不要新建表。
