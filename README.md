# fuxi-ailabs.github.io

组织站点 https://fuxi-ailabs.github.io/ 。

| 路径 | 内容 |
|---|---|
| `releases/` | HMBot(资源包)和 hmigbot-plus(工具包)的发版按时间排在一起,每个 release 一张卡,卡头标签区分产品;记录迁移评测台账:每个版本下按 App 记录功能完备度、可用性、准确率、代码完备度、时间、token 与证据链接,每个 App 下可展开功能清单 |
| `releases/evals.json` | 评测数据(版本 × App 一行),用 `tools/add_eval.py` 追加,不手改 |
| `tools/add_eval.py` | 追加一行评测:`--session-dir`(hmbot-session-archive 的输出目录,自动取时间 / token / 归档链接)、`--gt-eval`(gt-eval Eval Result,自动取功能完备度)、`--functional`(a2h-replay-bench functional json),其余指标 `--usability 0.6` / `--accuracy 90%` / `--code-completeness 26/31` 手填 |
| `tools/build_releases.py` | 从 `fuxi-ailabs/HMBot` 的 Releases(安装包直链、源码 commit、a2h 版本)加上 `releases/evals.json` 生成 `releases/releases.json` 和 `releases/index.html`;不收集 CI 验证记录 |
| `tools/build-releases.workflow.yml` | 每日自动重建的 workflow 定义。推送它需要带 `workflow` scope 的 token(`gh auth refresh -s workflow`),然后移到 `.github/workflows/`;运行还需要仓库 secret `HMBOT_TOKEN`(对 HMBot 有 contents:read + actions:read 的 PAT) |

## 本地重建

```bash
gh auth status                     # 需要能读 fuxi-ailabs/HMBot
python3 tools/build_releases.py    # 只读 Releases 元数据,不下载 artifacts
git add releases && git commit -m "releases: refresh" && git push
```

发版后记一条迁移评测(时间 / token 来自会话归档,四个比例来自评测):

```bash
python3 tools/add_eval.py --tag v1.8.1 --app Jetsnack --session-dir ~/Downloads/jetsnack-complete-session-<UTC> \
    --functional-completeness 26/31 --usability 0.6 --accuracy 90% --code-completeness 0.95 --model glm-5.3 --host claude
python3 tools/build_releases.py && git add releases && git commit -m "evals: v1.8.1 Jetsnack" && git push
```

六个指标的口径:功能完备度 = 功能在鸿蒙上可达的比例(gt-eval 严格完备率 / bench 严格口径,测不了计失败);可用性 = 按普通用户正常使用可用的比例(卡死 / 崩溃绕不过去的算不可用);准确率 = 行为 / 业务对比与安卓一致的比例;代码完备度 = 源码单元有迁移对应物且无桩的比例;时间 = 迁移首末会话记录的墙钟;token = 全部 API 调用 input + cache read + cache write + output 之和。

功能清单一列是人工上传的:一个链接,或一个表格文件。

```bash
# 链接(共享文档、仓库里的文件等)
python3 tools/add_eval.py --tag v1.8.1 --app Jetsnack --checklist-url https://...
# 表格文件:复制到 releases/checklists/<tag>/<app>.<ext> 并从页面链接。markdown 表 | ID | 功能 | 结果 | 说明 |、
# JSON 列表 [{id,feature,result,note}]、a2h-replay-bench functional-*.json 会被解析,页面给出统计和页内预览;
# 其他格式(xlsx / pdf / html ...)原样上传并链接
python3 tools/add_eval.py --tag v1.8.1 --app Jetsnack --checklist checklist.md --checklist-source "张三 2026-11-01 真机评测"
```

用 hmigbot-plus 工具包(hmbot CLI)跑的迁移,给 `--hmbot-run <hmigbot-plus>/examples/cli-migration-matrix/runs/<run>`:app、模型、宿主、工具包版本(→ `--plus-tag`,行挂到 hmigbot-plus 的那个 release 下)、墙钟和实际跑过的能力模块都从 manifest.yaml 读;同时给 `--tag` 时也挂到 HMBot 的 release 下。

能力组合一列记录这次迁移开了哪些能力模块(core / ota / telemetry / memory / ecat / dt / visual / hitest / 1to1 / arch)。给了 `--project` 时自动探测 core、1to1 / arch(安装 profile)、ota(`.migbot/release.json`)、telemetry(`telemetry_consent`)、memory(`.claude/a2h-bench/` 或 mbm 插件);ecat / dt / visual / hitest 是 hmbot CLI 的能力,用 `--cap ecat --cap visual` 手工补。页首有十个模块的职责 / 价值说明。

评估方法一列(功能清单右边)同样人工上传:`--method https://...` 链接、`--method 方法.md` 文件(复制到 `releases/methods/<tag>/`)、或 `--method "真机逐项走查"` 一句话。

结果列写 通过 / 部分 / 不通过 / 测不了 / 未评测。解析出的清单有结论而没传 `--functional-completeness` 时,功能完备度 = 通过数 ÷ 已评测数(测不了计失败)。

页面里指向私有仓的链接(安装包、sha256)只有登录的组织成员能打开;页面本身是公开的,
所以只放结论和链接,不放报告原文。
