# fuxi-ailabs.github.io

组织站点 https://fuxi-ailabs.github.io/ 。

| 路径 | 内容 |
|---|---|
| `releases/` | HMBot 每次发版的迁移评测台账:每个版本下按 App 记录功能完备度、可用性、准确率、代码完备度、时间、token 与证据链接,每个 App 下可展开功能清单 |
| `releases/evals.json` | 评测数据(版本 × App 一行),用 `tools/add_eval.py` 追加,不手改 |
| `tools/add_eval.py` | 追加一行评测:`--session-dir`(hmbot-session-archive 的输出目录,自动取时间 / token / 归档链接)、`--gt-eval`(gt-eval Eval Result,自动取功能完备度)、`--functional`(a2h-replay-bench functional json),其余指标 `--usability 0.6` / `--accuracy 90%` / `--code-completeness 26/31` 手填 |
| `tools/build_releases.py` | 从 `fuxi-ailabs/HMBot` 的 Releases 与 Release Notes 加上 `releases/evals.json` 生成 `releases/releases.json` 和 `releases/index.html` |
| `releases/overrides.json` | 手工补充的报告链接 / 备注,按 tag 归档;生成时合并进去 |
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

每个 App 一份功能清单:评测行下面可展开,逐条功能记 通过 / 部分 / 不通过 / 测不了 / 未评测。来源三选一:

```bash
# 1) 只列清单(spec 阶段的 feature-index.md,全部记为未评测)
python3 tools/add_eval.py --tag v1.8.0-rc.1 --app Jetsnack --feature-index <工程>/spec/baseline/feature-index.md
# 2) a2h-replay-bench 的 functional-*.json(items 的 result),功能文案来自 FUNCTIONAL.md
python3 tools/add_eval.py --tag v1.8.0-rc.1 --app Jetsnack --checklist results/jetsnack/functional-xxx.json --checklist-names FUNCTIONAL.md
# 3) 自己写的 markdown 表 | ID | 功能 | 结果 | 说明 |,或 JSON 列表 [{id,feature,result,note}]
python3 tools/add_eval.py --tag v1.8.0-rc.1 --app Jetsnack --checklist checklist.md
```

清单里有结论而没传 `--functional-completeness` 时,功能完备度 = 通过数 ÷ 已评测数(测不了计失败)。

补一条手工报告链接:在 `releases/overrides.json` 对应 tag 下加 `{"title","url","note"}`,重跑脚本。

页面里指向私有仓的链接(Actions artifacts、Release assets、docs)只有登录的组织成员能打开;页面本身是公开的,
所以只放结论和链接,不放报告原文。
