# fuxi-ailabs.github.io

组织站点 https://fuxi-ailabs.github.io/ 。

| 路径 | 内容 |
|---|---|
| `releases/` | HMBot 每次发版对应的测试报告台账:验收 Actions run、各产品×平台的 verification / smoke / 审计 / 官方升级结果、全部报告文件链接 |
| `tools/build_releases.py` | 从 `fuxi-ailabs/HMBot` 的 Releases、Release Notes、Actions run 与 verification artifacts 生成 `releases/releases.json` 和 `releases/index.html` |
| `releases/overrides.json` | 手工补充的报告链接 / 备注,按 tag 归档;生成时合并进去 |
| `tools/build-releases.workflow.yml` | 每日自动重建的 workflow 定义。推送它需要带 `workflow` scope 的 token(`gh auth refresh -s workflow`),然后移到 `.github/workflows/`;运行还需要仓库 secret `HMBOT_TOKEN`(对 HMBot 有 contents:read + actions:read 的 PAT) |

## 本地重建

```bash
gh auth status                     # 需要能读 fuxi-ailabs/HMBot
python3 tools/build_releases.py    # 首次会把各 run 的 verification artifacts 下载到 ~/.cache/hmbot-release-reports
git add releases && git commit -m "releases: refresh" && git push
```

发版后补一条手工链接:在 `releases/overrides.json` 对应 tag 下加 `{"title","url","note"}`,重跑脚本。

页面里指向私有仓的链接(Actions artifacts、Release assets、docs)只有登录的组织成员能打开;页面本身是公开的,
所以只放结论和链接,不放报告原文。
