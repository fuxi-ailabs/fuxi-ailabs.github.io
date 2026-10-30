# fuxi-ailabs.github.io

组织站点 https://fuxi-ailabs.github.io/ 。

| 路径 | 内容 |
|---|---|
| `releases/` | HMBot 每次发版的迁移评测台账:每个版本下按 App 记录功能完备度、可用性、准确率、代码完备度、时间、token 与证据链接;发布流水线的构建验收折叠在卡底 |
| `releases/evals.json` | 评测数据(版本 × App 一行),用 `tools/add_eval.py` 追加,不手改 |
| `tools/add_eval.py` | 追加一行评测:`--session-dir`(hmbot-session-archive 的输出目录,自动取时间 / token / 归档链接)、`--gt-eval`(gt-eval Eval Result,自动取功能完备度)、`--functional`(a2h-replay-bench functional json),其余指标 `--usability 0.6` / `--accuracy 90%` / `--code-completeness 26/31` 手填 |
| `tools/build_releases.py` | 从 `fuxi-ailabs/HMBot` 的 Releases、Release Notes、Actions run 与 verification artifacts 生成 `releases/releases.json` 和 `releases/index.html` |
| `releases/overrides.json` | 手工补充的报告链接 / 备注,按 tag 归档;生成时合并进去 |
| `tools/build-releases.workflow.yml` | 每日自动重建的 workflow 定义。推送它需要带 `workflow` scope 的 token(`gh auth refresh -s workflow`),然后移到 `.github/workflows/`;运行还需要仓库 secret `HMBOT_TOKEN`(对 HMBot 有 contents:read + actions:read 的 PAT) |

## 本地重建

```bash
gh auth status                     # 需要能读 fuxi-ailabs/HMBot
python3 tools/build_releases.py    # 首次会把各 run 的 verification artifacts 下载到 ~/.cache/hmbot-release-reports
git add releases && git commit -m "releases: refresh" && git push
```

发版后记一条迁移评测(时间 / token 来自会话归档,四个比例来自评测):

```bash
python3 tools/add_eval.py --tag v1.8.1 --app Jetsnack --session-dir ~/Downloads/jetsnack-complete-session-<UTC> \
    --functional-completeness 26/31 --usability 0.6 --accuracy 90% --code-completeness 0.95 --model glm-5.3 --host claude
python3 tools/build_releases.py && git add releases && git commit -m "evals: v1.8.1 Jetsnack" && git push
```

六个指标的口径:功能完备度 = 功能在鸿蒙上可达的比例(gt-eval 严格完备率 / bench 严格口径,测不了计失败);可用性 = 按普通用户正常使用可用的比例(卡死 / 崩溃绕不过去的算不可用);准确率 = 行为 / 业务对比与安卓一致的比例;代码完备度 = 源码单元有迁移对应物且无桩的比例;时间 = 迁移首末会话记录的墙钟;token = 全部 API 调用 input + cache read + cache write + output 之和。

补一条手工报告链接:在 `releases/overrides.json` 对应 tag 下加 `{"title","url","note"}`,重跑脚本。

页面里指向私有仓的链接(Actions artifacts、Release assets、docs)只有登录的组织成员能打开;页面本身是公开的,
所以只放结论和链接,不放报告原文。
