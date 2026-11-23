# 竞彩篮球赛前推盘预测模拟器（GitHub Actions 多路自动同步版）
# Jingcai Basketball Pre-Match Prediction Simulator (GitHub Actions Multi-Channel Auto-Sync)

基于单文件 HTML + GitHub Actions 多路同步架构构建，覆盖竞彩篮球**胜负、让分胜负、大小分、12项胜分差、混合过关**、四维深度分析（基本面 / 数据面 / 冷门预警 / 最终研判）、推荐组合方案（2稳胆+1分差高赔 & 3场纯分差）与每日复盘区。

## 多路同步通道架构

本仓库内置 **2 路以上赔率通道 + 2 路以上赛果通道**，自动容灾兜底：

1. **后端 GitHub Actions 定时同步（`scripts/sync_basketball_data.py`）**
   - **赔率/赛程通道 1**：中国体育彩票官方竞彩篮球接口（`web-api.sporttery.cn`）
   - **赔率/赛程通道 2**：网易体育竞彩篮球页面（`sports.163.com/caipiao/match/basketball/jclq`）兜底
   - **赛果通道 1**：中国体育彩票官方竞彩篮球赛果开奖接口
   - **赛果通道 2**：ESPN NBA 官方公开计分板接口（`site.api.espn.com`），自动回填 NBA 完场比分与最新赛季战绩
2. **前端双路实时拉取 + 一键导入**
   - 页面打开及点击右上角「**同步实时赔率**」时，依次拉取仓库最新 `data/matches.json` 并直连 ESPN NBA 公开比分接口校验当日赛果与战绩。
   - 点击「**导入/解析网页数据**」支持一键上传/粘贴 `matches.json`、一键批量导入完场赛果（自动结算四玩法与复盘区）、以及一键批量解析网页赔率行。

## 上传与启用步骤

1. 在你的 GitHub 账号下新建公开仓库（例如 `laupak-basketball`）。
2. 将压缩包解压后的全部文件（`index.html`、`data/matches.json`、`scripts/sync_basketball_data.py`、`.github/workflows/sync.yml`、`README.md`）上传到仓库根目录。
3. 进入仓库顶部 **Settings → Pages**，将 Source 设为 **Deploy from a branch**，选择 `main` 分支 `/ (root)` 并保存，等待约 1 分钟即可通过 `https://bl17784999986-ai.github.io/laupak-basketball/` 在电脑与手机端访问。
4. 进入仓库顶部 **Actions** 选项卡，若提示启用 Workflows 请点击开启；之后每天北京时间 **07:00、11:30、14:30、18:00、22:00** 会自动同步最新赛事、赔率与赛果，也可随时点击 **Sync Basketball JC Schedule, Odds & Results → Run workflow** 手动立即同步。
