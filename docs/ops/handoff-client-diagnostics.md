# 派工单 B：让「交上去」在手机上可诊断（为新投递通道的真机验证做准备）

> 这张单子**可以直接整段贴给另一个 AI harness**（DeepSeek 之类）去做。
> 做完它把 **diff / PR** 交回来，**不要它推 main、不要它碰 gh-pages**。

---

## 【给 DeepSeek 的任务】

你在改一个开源小项目：**训练家图鉴**（`https://github.com/lilia-shound/pokemon-dex`，
站点 `https://lilia-shound.github.io/pokemon-dex/`）——
「读满一定时长 → 随机收服一只宝可梦」的进度记录器。
你负责**三个子页面的「投递」那一段**：把用户这段时间的投入量报给服务端。

### 背景（你需要知道的）

- 项目有四个数据源：**微信读书**（服务端自己拉）、**番茄钟**、**背单词**、**KOReader**。
  后三个是**用户端报上来的**（push 源）。
- **两条投递通道**：
  1. **老通道**：客户端打开 GitHub 的「新建 issue」页面，人点 Submit ⇒ 报告变成一个 issue。
  2. **新通道（S8，免登录直传）**：客户端 **直接 POST** 到一个公网接收端
     `https://pokemon-dex-ingest.pages.dev/ingest`（服务端在 Cloudflare Pages Functions 上），
     免登录、免开 issue。服务端 cron 每 3 小时把队列拉下来、校验、并进账本。
- **三个子页**（番茄钟 / 背单词 / KOReader 落地页）**同源**（都在 `site/` 下），
  每个页面**只许引一个 `<script>`** ⇒ **投递逻辑在三个子页里是逐字重复的**（不能抽公共模块）。
  仓库里有防漂移测试盯着这件事：`tests/test_ingest_drift.py`。
- ★ **现状**：新通道**在真机上一次都没跑过**（用户实际交的那一次走的是老通道）。
  所以现在「交上去」这件事在手机上**没法判断到底成没成、卡在哪一环**。

### 这次要做的事（就这三件，别多做）

1. **成功时多说一句**：除了现在的「已记录 ✓」，再显示**服务端返回的条目 id** 和**提交时刻**，
   方便用户截图给维护者（一眼能确认「这条是哪一条、什么时候交的」）。
2. **失败时说得清**：区分并显示
   **超时 / 网络不可达 / 服务端 4xx / 服务端 5xx / 凭证不对**，
   而不是像现在这样只丢一句「网络错误」。★ 失败后**照旧进待发队列**（行为别变）。
3. **加一个「诊断模式」**：用 URL 参数打开（`?diag=1`）或页面上一个不显眼的小开关，
   打开后把**要发的 payload、请求耗时、HTTP 状态、响应体原样**显示在页面上。
   目标是：**用户在手机上点一次、截一张图，维护者就能看到全链路**，
   不用问「你网络通不通」「你点的哪个按钮」。

### 硬性约束

- **三个子页的投递逻辑必须保持逐字一致**（防漂移测试会红）。
  ⇒ 你要改就**三处一起改**，改完跑 `tests/test_ingest_drift.py` 确认没漂。
- **改了 `app.js` 就必须 bump 版本号**（GitHub Pages 的 CDN 会缓存旧文件）：
  - 三个子页的 `index.html` 里 `app.js?v=N` 要 `N+1`；
  - **番茄钟页还多两处**：Service Worker 的缓存名 `pomodoro-shell-vN`
    （`site/pomodoro/sw.js` 和 `site/pomodoro/app.js` 里各有一份，**两处都要改**），
    以及 `site/pomodoro/app.js` 里 `register('sw.js?v=N')` 的 `N`。
  - 仓库里有守卫测试盯着这件事（`tests/test_worker_client.py` 里的 `ScriptVersionGuardTest`）。
- **不许改**：账本与口径（`data/`）、结算与发布脚本（`scripts/award.py`、
  `scripts/deploy.sh`）、接收端（`worker/`）、CI（`.github/workflows/`）。
  ★ 你只改 `site/` 里那三个子页的 `app.js` / `index.html`，以及 `tests/` 里相关测试。
- **不许推 main、不许碰 gh-pages、不许跑 `scripts/deploy.sh`** ——
  发布只有一条路径，由另一个人执行（两个写者会打架）。交回 **diff 或 PR** 就行。

### 验收标准（必须全绿）

```bash
git clone --depth 1 https://github.com/lilia-shound/pokemon-dex.git
cd pokemon-dex

# 整套测试（零依赖，标准库 unittest）
python3 -B run_tests.py

# 只跑相关的
python3 -B -m tests.test_worker_client
python3 -B -m tests.test_ingest_drift
```

- ✅ 整套**全绿**、且**一个测试都不能被跳过**。
  ★ CI 有一道「不许有测试被静默跳过」的检查（`scripts/check_no_skipped.py`）：
  依赖没装好时 unittest 会 *skip* 而不是 *error* ⇒ **少测几十条也是绿的**。
  所以**别新写「装不上就 skip」的分支**。
- ✅ **新加的浏览器断言要真的跑起来**（Playwright 在 CI 里是装好的：
  `pip install playwright` + `python -m playwright install --with-deps chromium`）。
  本地如果没装 chromium，照 `tests/browser_path.py` 的 `chromium_path()` 找，
  **别写死版本号路径**（沙箱刷新会换 Playwright 版本）。
- ✅ 你新加的诊断模式，**要有一个测试证明它打开后能看到 payload 与响应**。

### 你要改/加的文件（现成的骨架，别重新发明）

| 文件 | 是什么 |
|---|---|
| `site/pomodoro/app.js` | 番茄钟页（**它有 Service Worker**，改它要动缓存名） |
| `site/words/app.js` | 背单词页 |
| `site/koreader/app.js` | KOReader 落地页 |
| 三个 `site/*/index.html` | 各自引 `app.js?v=N` |
| `site/pomodoro/sw.js` | 番茄钟的 Service Worker（缓存名 + `register('sw.js?v=N')`） |
| `tests/test_worker_client.py` | 三个子页的浏览器观感断言（点同步 ⇒ 一个 POST + 原地「已记录」；服务端 500 ⇒ 提示 + 进待发队列）+ 版本号守卫 |
| `tests/test_ingest_drift.py` | 防漂移：三页投递逻辑必须逐字一致 |

### 提交规矩

- **一次提交只做一件事**，commit message **中文、动词开头**（例：`feat: 投递失败时显示状态并加诊断模式`）。
- 中文注释与 docstring。
- 交回方式：**给 diff 或开 PR**，说明改了哪几个文件、为什么、测试怎么跑绿的。

### 一句话交付

「用户在手机上点一次『交上去』，不管成没成都能看到**发生了什么**；
失败时能分辨是超时还是服务端出错，打开诊断模式还能看到 payload 和响应原文。」

---

## 维护者备注（给姜来，不用贴给 DeepSeek）

- 这张单子的目的是**给你自己**的：做完之后你在手机上点一次、截一张图，我就能确认新通道在真机上通没通。
- 做完由**我** review、合并、部署（顺便 bump 版本号，别让 CDN 吃旧文件）。
- 待办里还有：**KOReader 插件的真机菜单**（工具 → 更多工具 → 训练家图鉴）—— 那个沙箱里没法验，只能你点。
