# 评价洞察（review-analytics）

pLeace 门店顾客评价的分析工具。数据来自现有的「顾客说」系统（明道云），本工具负责统一口径、用 Claude 逐条解析、再做多维度的看板。

## 它解决什么问题

| 现有工具的局限 | 这里怎么做 |
| --- | --- |
| "差评"在不同页面有 4 种算法 | 全局一个口径：星级 ≤ 3，或 AI 判定总体消极；页面顶部写明 |
| 同一家店有多种名称写法 | 按括号里的分店名归一，13 家店各一条记录，别名表可扩 |
| AI 情绪有 15 个选项，"中性偏正面 / 中性偏正向"混在一起 | 固定 3 个值；细项固定 30 个（口味、分量、上菜速度……），不允许自由发挥 |
| 好评里的小毛病被整条"积极"盖住 | 每条评价里每个被提到的点单独打分，4 星好评里的"肉酱偏咸"记成一条负面的 菜品·口味 |
| 菜名不归一 | 菜名去符号、去前缀后聚合，另有别名表 |
| 只有单维度计数 | 门店 × 细项热力图、菜品 × 吐槽点、按时段 / 场景 / 星期 / 顾客等级 / 新老客的差评率、口碑与曝光成交的对照、差评激增预警 |

## 目录

```
app/
  db.py             SQLite 建表（reviews / review_analysis / review_aspects / daily_metrics …）
  normalize.py      门店名、菜名归一
  importers.py      从 顾客说 导入评价（ndjson 或 Excel 导出）
  import_metrics.py 导入每店每日经营数据（曝光、访问、成交、评分）
  analyze.py        Claude 逐条解析：同步 / Batch 两种模式
  queries.py        看板用的聚合查询
  server.py         FastAPI 接口 + 静态页面
web/index.html      看板（ECharts，已内置 web/echarts.min.js）
scripts/
  login_portal.js   用手机验证码登录 顾客说，保存会话
  pull_portal.js    用保存的会话把整张表拉成 ndjson
```

## 安装

```bash
cd review-analytics
pip install -r requirements.txt
npm install -g playwright      # 只有用 scripts/ 拉数据时需要
```

## 第一步：导入数据

方式 A，直接从 顾客说 拉取（需要能登录的账号）：

```bash
node scripts/login_portal.js                 # 手机验证码登录，会话存到 scripts/state.json
cd scripts
node pull_portal.js reviews x 6882d4c699553aa3d9aea3fa 6882d4c699553aa3d9aea3fb 200
node pull_portal.js traffic x 6894da1fdf282a4cfe62d7f7 6894da5ef112c312aed5374e 500
cd ..
python -m app.importers scripts/data/reviews.ndjson
python -m app.import_metrics scripts/data/traffic.ndjson
```

方式 B，在 顾客说 里把「顾客评价」表导出为 Excel，然后：

```bash
python -m app.importers 顾客评价.xlsx
```

重复导入是安全的：按记录 ID 去重更新。

## 第二步：AI 解析

需要 `ANTHROPIC_API_KEY`。先估价，再小批量试跑，看结果满意再全量：

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python -m app.analyze estimate --since 2026-06-28 --model claude-sonnet-5   # 待分析条数、大致费用
python -m app.analyze run --limit 20 --since 2026-06-28 --model claude-sonnet-5   # 同步跑 20 条，立刻能在看板里看到
python -m app.analyze batch --since 2026-06-28 --model claude-sonnet-5      # 提交 Batch（半价，通常 1 小时内完成）
python -m app.analyze collect                   # 批次完成后收取结果
python -m app.analyze status
```

默认跳过 40 字以内的 5 星短评（"好吃""不错"这类，约占两成，几乎没有信息量），加 `--all` 可以包含。`--since` 限定起始日期，不加就是全部。默认模型 `claude-opus-5`，`--model claude-sonnet-5` 便宜一半以上。解析结果的结构见 `app/analyze.py` 里的 `ReviewAnalysis`。

`data/` 目录不入库（里面是顾客评价原文），换机器要重新走第一步导入。

## 第三步：看板

```bash
uvicorn app.server:app --port 8000
```

打开 http://localhost:8000 。页面顶部选时间、门店、平台，所有图表一起刷新；每个指标旁都有"对比上一周期"。

接口：`/api/overview` `/api/trend` `/api/stores` `/api/aspects` `/api/dishes` `/api/segments` `/api/business` `/api/alerts` `/api/reviews`，参数 `from`、`to`、`stores`、`platform`。

## 日常更新

每天（或每周）重复"拉取 → 导入 → analyze batch → collect"即可，都是增量的。可以放进 cron。
