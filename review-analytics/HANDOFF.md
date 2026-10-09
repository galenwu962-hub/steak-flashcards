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

- **中差评（用户 2026-10-09 改定）**：4 星以下（rating < 4，含 3.5 星）一律算中差评，不管内容多正面；
  4 星及以上只在指出具体问题时才算（某道菜、服务环节、等位、环境、卫生、某样东西的价格），「一般」「中规中矩」「没惊喜」不算。
  只有 AI 判为消极/中性的 4 星以上评价才送去判（app/complaints.py → review_judgments 表），未判的暂按 AI 情绪。
  例行：`python -m app.complaints batch --from A --to B` → `python -m app.batchjobs collect`。queries.NEG_EXPR 是唯一定义。
  起因：总办合伙人质疑天环广场数据（营运说 8 月 9.4% → 9 月 3.6%），我们任何口径都对不上，天环 9 月仍在变差。门店分档：< 8% 优秀，8%–12% 正常，> 12% 问题。
- AI 解析用 `claude-sonnet-5`，Batch 模式，默认跳过 40 字内的 5 星短评。
- **用户规矩（2026-09-27）：例行更新一律用 Batch（半价，等几十分钟到几小时）**，只有用户当场等结果时才用同步调用。
  逐条解析：`python -m app.analyze batch` → `collect`。归纳三步（2026-09-27 加上）：
  `python -m app.actions batch --from A --to B --chain` 一次提交，然后每 15 分钟 `python -m app.batchjobs collect`（用 send_later），
  收到改善事项后自动提交跨店主题，收到主题后自动提交可落地审核，直到提示「全部完成」。批次记录在 llm_jobs 表。
- 回复建议（app/reply.py + app/knowledge.py）：店长口吻，像朋友，不卑微；**回复里不解释原因、不用内部术语、不自我诊断**；
  预制菜问题不正面回答，虚心接受；不承诺任何补偿（补救政策待营运确认）；退款和严重投诉引导打门店电话找店长。
- 改善事项（app/actions.py）：按门店把负面意见（含好评里的抱怨）归纳成 3–8 条，带负责角色（店长 / 品控 / 区域经理）、动作、顾客原话；
  食安、虫害、卫生一律单列并标升级。
- 可落地审核（app/actionability.py，用户 2026-09-26 定）：店长拿到事项必须知道改哪里（具体菜、时段、环节、岗位、设备）。
  笼统的（"上菜慢""贵""一般"）先回评价原文找具体信息改写；原文也没有的判「仅知晓」，只让店长留意，不设截止日期、不提醒、不计入完成率。
  食安卫生类不能判仅知晓。每次生成事项后都要跑一遍。
- 「相关评价数」= 事项背后有几条评价（去重），不是原话句数（evidence_count 是句数）。
- 分类与跨店主题（app/themes.py）：9 个固定分类；覆盖 ≥ 3 家店的问题算跨店主题。
- 组织：杨丽娜（Nina，资深运营督导，userId 17454583675472494）、张异香（阿香，高级食安品控经理，userId 17629152145383692）。店长名单待杨丽娜提供。
  2026-10-08 用户让我把 9 月月报图片单聊发给了她们两位（`dws chat message send --user <id> --msg-type file --file ...`）。

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
- 改善事项清单钉钉云文档「pLeace 改善事项清单」：https://alidocs.dingtalk.com/i/nodes/wva2dxOW4YQA3ZL4SYn7DvvnVbkz3BRL （初版已按用户要求删除）
  **用户规矩：以后更新一律在这份文档上直接改，不要再新建文档。** `doc +import` 只会新建，所以更新时从 items.json 生成 Markdown
  （表格用 Markdown 表格），用 `dws doc +checkpoint-update`（先存版本再覆盖）或 `dws doc +update --node <上面的 ID> --command overwrite --content @<相对路径>.md`，
  改完 `doc +fetch` 回读核对。Word 版（report_docx.js）仍可生成给要下载的人。
  已实现：`python -m app.export_items --from A --to B --out data/reports/items-X.json` → `python -m app.report_md <json> data/reports/改善事项清单.md`
  → `dws doc +checkpoint-update --node <ID> --mode overwrite --content "@data/reports/改善事项清单.md" --yes`。
- 2026-09-27 用户要求把清单时间段扩到 9/1–9/25：重新跑了 actions / themes / actionability（104 条，15 条红线，2 条仅知晓，10 个主题），
  云文档已原地覆盖；AI 表格也已按用户同意换成这 104 条（先建新的再删旧的 94 条，旧的无人改过）。102 条整改带截止日期，2 条仅知晓无截止日期。
  weekly.py 取「最近生成的那一期」（按 created_at），即 9/1–9/25，和表格一致。
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

- 表格「门店改善事项跟进」：https://alidocs.dingtalk.com/i/nodes/Amq4vjg890Dmk3B3sxYMD45nJ3kdP0wQ ，现为 9/1–9/25 的 104 条事项，
  字段「事项编号」= action_items.id。下发日期统一 9-28；截止日期 = 红线 +1 天、高 +3、中 +7、低 +14。负责人字段暂空，等杨丽娜给店长名单后填。
- 视图：全部事项（按门店）/ 处理看板（按状态，只含「整改」）/ 逾期事项 / 红线事项 / 仅知晓（留意即可）；仪表盘「改善事项进度」6 个图表，
  完成进度、各门店处理情况、未完成分类都只算「整改」。
- 可落地审核：仅知晓的事项不设截止日期，所以不会触发提醒。
  日期清空要传 ""（传 null 无效）；单选字段清不掉，所以仅知晓的状态仍显示待处理，靠「类型」区分。字段：类型 Rxzh58D，说明 WlgwxOy，相关评价数 Jzs930T。
- 自动化（已启用）：到期当天提醒、逾期一天提醒（有负责人发负责人+管理员，没有只发管理员）、每周一逾期汇总（只发管理员）。
  管理员 = 流程创建人（吴之洋）。以后交营运管理时要把流程创建人/接收人换成营运。
- 周报进度：先 `python -m app.sync_dingtalk` 把表格状态拉回数据库，再 `python -m app.weekly`。
- 2026-09-28 已给张异香（userId 17629152145383692，高级食安品控经理）开「可查看」权限并单聊发了链接（`dws doc +grant-and-share`）。
- 下一期事项要追加到同一张表（record create，字段 ID 见 app/sync_dingtalk.py 和 scratch 里的写法），不要新建表。

## 7. 90 天评价洞察报告（2026-09-27）

- 用户想让 5,367 条逐条解析（约 11 美元）有一份成果：钉钉云文档「pLeace 顾客评价 90 天洞察（6/28–9/25）」
  https://alidocs.dingtalk.com/i/nodes/nYMoO1rWxaXaAR2PS9R9k51rV47Z3je9 。只用 SQL 汇总已有解析结果，没有再调 API。
- 源文件 data/reports/90天评价洞察.md（不入库）。要点：中差评率 7 月 12.6% → 8/9 月 10.9%，8 月中后停滞；4 家店 >12%，天环广场唯一变差；
  负面占比最高的是排队、空调、噪音、菜品温度、座位；腹心肉是全公司被吐槽最多的菜；周日最差；杭州恒隆卫生类投诉条数最多。
- 更新同一份文档时照第 4 节规矩原地覆盖，不新建。

## 8. 腹心肉改良（2026-09-27，Q4 研发部 × 厨政部重点）

- 云文档「椒麻炙烤腹心肉复核建议（研发部 × 厨政部）」：https://alidocs.dingtalk.com/i/nodes/P0MALyR8klYlmMwpIDM4EBo2W3bzYmDO （内嵌信息图）。
- 信息图源文件 data/reports/fuxin_infographic.html，PNG 用 Playwright 1920×1080、deviceScaleFactor 2 截图（浏览器走 $HTTPS_PROXY 加载 Google Fonts）。
- 旧口径（已停用于考核）：菜名含「腹心」的全部合并；90 天 920 个评价点、负面 147（35% 熟度口感、22% 薯条、8% 偏咸…，按原话关键词互斥归类）；负面点占比 16%。
  2026-10-05 已把复核文档、信息图都改成新口径（9 月 8.7% → Q4 目标 <5%，门店对比和每周趋势也按新口径重算）。
- 周报 app/weekly.py 的 TRACKED_DISHES 里追踪这道菜，要加别的重点菜品就往里加一行。
- 2026-09-28 周会补充（张蓓/采购）：多店反馈来料腹心肉肉条过细无法使用；已定无法使用部分供应商赔偿、持续开发新厂号和供应商。
  文档和信息图已加「采购部」一栏。
- **这份文档已不只是我写的**：顶部是别人加的「9/30 线上培训会简报 · 腹心肉 1.1 版」（会议决定、各部门行动、下店检查表），
  「五、建议」里薯条和酱汁两条也被人改过（Q4 先不动）。只改自己负责的部分，不要整篇覆盖。
- 文档块编辑的坑（2026-10-05 踩过）：`block_replace`/`block_insert_*` 用 jsonml 插表格会**先删原块、新块却写不进去**；
  用 markdown 插表格只会变成带「|」的纯文字。可靠做法：`--command append` 把新内容（Markdown，表格能正确转换）追加到文末 →
  `block_copy_insert_after --src-block-ids X --block-id X --after-block-id 锚点` 一块一块复制到位（多个源 ID 只会复制第一块）→
  `block_delete` 删旧块和文末暂存块。回读验证经常报失败但其实已写入，重试前先 `+fetch --detail with-ids` 看实际内容。
  换图：`+media-insert --ref-block <前一块> --where after` 插新图，再删旧图块。

## 9. 腹心肉考核口径（2026-10-05 定，Q4 店总 × 总厨 KPI）

- 起因：品控张异香手工过了 9 月全部点评，算出 8.7%；和旧口径 16% 对不上。两个都没错，旧口径按评价点算、连薯条和小建议都算。
- 新口径「明确不满率」= 对这道菜本身明确不满的评价 ÷ 提到这道菜的评价（app/dish_kpi.py，结果存 dish_judgments 表）。
  用户逐条审定的标准：只嫌薯条/酱汁/熟度选项、好评里的建议语气（「如果更嫩一些就好了」）、夸贬参半 → 不算；
  熟度跟点的不一致（「选七分实际九分」，即使说嫩）、「性价比普通+薯条又油又干」→ 算。
  「中规中矩」「没觉得什么感觉」「肉一般吧，但性价比不错」这类不温不火的，暂按不算，**待用户确认**。
- 9 月全月：208 条提到、18 条明确不满 = 8.7%，与异香手工数一致。**Q4 目标：5% 以下（用户 2026-10-05 定）**，weekly.TRACKED_DISHES 的 target = 0.05。
- 对这道菜只有正面评价点的直接按规则判不算；其余逐条交模型判。例行：`python -m app.dish_kpi batch --from A --to B` → `python -m app.batchjobs collect`。
  `python -m app.dish_kpi show --from A --to B` 列出所有判为不满的评价，给品控抽查。
- 门店一个月提到这道菜多在 30 条以下，建议门店按季度累计考核。

## 10. 周报和月报（每周一出周报，每月第一个周一加出月报）

- 流程：`bash scripts/portal.sh pull`（登录失效才要验证码）→ `python -m app.analyze batch --since <上月1日> --model claude-sonnet-5`
  → `python -m app.dish_kpi batch ...` → 轮询 `analyze collect` 和 `batchjobs collect` → `python -m app.sync_dingtalk`
  → `python -m app.weekly --to <上周日>`；月报 `python -m app.monthly --month YYYY-MM --notes <要点文件>`（要点一行一条，手写）。
- 周报新增：本周高风险评价（review_analysis.risk = high，还没归纳进事项的，标「新评价」）；某天没评价或不足 5 条时顶部提示数据不完整。
- PDF 和图片：`node scripts/render_report.js <html> --pdf <out.pdf> --png <out.png>`，一整页长版（宽 960px，图片 2 倍清晰度），用户发微信给总办。
  **用户规矩（2026-10-08）：周报、月报生成后，直接把一页纸图片单聊发给张异香和杨丽娜（Nina），不用再问用户。**
  先发一句要点（全公司中差评率及变化、问题门店、腹心肉明确不满率对目标），再发图片：
  `dws chat message send --user <id> --content "<要点>"`，然后 `--msg-type file --file data/reports/<图片>.png`；
  用 `query-send-status` 确认送达。不给她们开文件夹权限（用户说暂时不需要）。数据不完整的周（如平台缺天）在要点里注明。
  周报的一页纸图片也要上传到下面的文件夹：`dws drive +upload --file data/reports/门店口碑周报_MMDD-MMDD.png --folder <folder id>`（2026-10-07 用户要求）。
- **钉钉文件夹「门店口碑周报·月报」**（我的文档根目录，用户 2026-10-05 要求专门存周报月报）：
  https://alidocs.dingtalk.com/i/nodes/1zknDm0WRaYaERL0SzXgK3Nw8BQEx5rG （folder id 1zknDm0WRaYaERL0SzXgK3Nw8BQEx5rG）。
  每份报告一篇云文档：`python -m app.report_doc weekly --to <周日> --out data/reports/docs/周报_MMDD-MMDD.md`（月报用 monthly）
  → `dws doc +import --file ... --folder <上面的 id> --name "门店口碑周报 · 9月28日–10月4日"`
  → `dws doc +media-insert --node <新文档> --file data/reports/<PDF> --file-view preview` 把 PDF 附在文末。
  已有：周报 9/21–9/27（按新口径重出）、周报 9/28–10/4、月报 2026 年 9 月。每期新建一篇，不覆盖旧的。
- 用户 2026-10-05 说明：① 改善事项的执行由品控张异香在她自己的 QSC 追踪表里跟，AI 表格的状态目前没人回填，
  完成率 0 不代表没做，别当成最紧急的问题报；② 9 月是全年最淡的月份，营业额比 8 月低 15%–20%，评价量少两成属正常。
- 2026-10-09：按新中差评规则重出了 9/21–9/27、9/28–10/4 两期周报和 9 月月报（文件夹里的云文档、图片、PDF 都已替换；
  替换图片用 `dws drive +upload --file ... --node <图片 id>`，不能同时带 --folder）。新规则下 9 月全公司 11.8%、问题门店 6 家、没有优秀门店，
  分档线（8% / 12%）是按旧口径定的，用户还没说要不要调。重出的月报**还没有**重新发给异香和 Nina（等用户看过）。
- 2026-10-09 再拉一次，顾客说平台仍然只到 10/1（10/4 只有 1 条），平台那边国庆后就没再采集，要用户去找顾客说的供应商。
- 2026-10-05：国庆期间顾客说平台 10/2–10/4 几乎没有数据（10/1 有 61 条，10/4 只有 1 条），周报已标注，数据补齐后要重出。

