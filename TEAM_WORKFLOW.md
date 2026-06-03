# 团队协作流程 / Team Workflow

> **为什么有这份文档.** 现在 PR 很乱:issue 描述不清、没人 triage、谁负责不明确、review 随缘、改了 agent 行为却只有 mock 单测、PR 之间互相不知道在改同一处。代码怎么写看 [`vibe-coding-guide.md`](vibe-coding-guide.md);**这份文档只管"事情怎么流转"**——谁提、谁认领、谁 review、记录到哪。
>
> 一句话规矩:**先有 issue,才有 PR;issue 必须 @ 到人;PR 必须 reference issue;两者都同步到飞书。**

---

## 核心流程(三步,所有人照做)

```
  ① 任何人发现问题 / 想做功能
        │  开 Issue(用模板,描述清楚,@ 相关人)
        ▼
  ② 负责人认领 → 开 PR(reference 该 issue)
        │  PR 描述用模板;@ 提 issue 的人一起 review
        ▼
  ③ Review 通过 + eval 通过 → 合并
        │  issue / PR 链接 reference 到飞书
        ▼
     飞书有完整记录:问题 → 谁做的 → 怎么解决的
```

### ① 提 Issue —— 描述清楚 + @ 人(所有人)

**任何问题、任何想法,先开 Issue,不要直接开 PR。** 没有对应 issue 的 PR 一律先补 issue。

用现成模板(`.github/ISSUE_TEMPLATE/`):`bug_report` / `feature_request` / `visualization_theme`。Issue 必须包含:

- **标题**:`[Bug]` / `[feature]` 前缀 + 一句话说清是哪个 stage / 哪个模块(例:`[Bug] Stage 6 critic 把 "no data" 当 PASS`)
- **描述清楚**:bug → 复现步骤 + 期望 vs 实际 + 日志;feature → 要解决什么问题(不是直接写方案)
- **@ 相关人**:在 issue 正文 @ 你认为该负责或该知道的人(见下方 [谁负责什么](#谁负责什么))
- **Label**:`bug` / `enhancement` / 对应 stage
- **不要**:标题含糊("流水线有问题")、没复现步骤、不 @ 任何人就扔在那

> 现状反例:open issue 里 #103/#96/#94 描述写得很好 ✅,但**没有 assignee、没 @ 人、没和后来修它的 PR 关联** ❌ —— 这正是要改的。

### ② 写 PR —— 负责人写,@ 提 issue 的人一起 review

- **认领**:负责人在 issue 上回复"我来"或被 @ 指派,issue 设 **Assignee = 自己**。
- **开 PR**:用 PR 模板(`.github/pull_request_template.md`),**正文第一行必须** `Closes #<issue>` 或 `Refs #<issue>` —— 把 PR 和 issue 焊死。
- **Review 名单**:`zhengxuyu`(org admin / 合并人)会被 auto-assign;**额外 @ 上提 issue 的那个人** —— 因为他最清楚问题背景,由他确认"这真的解决了我提的问题"。
- **改了 agent / stage 行为的 PR**:不能只有 mock 单测。必须说明**怎么验证 agent 行为真的变好了**(见 [Agent PR 的额外要求](#agent-pr-的额外要求))。

### ③ 合并 —— Review + eval 通过,然后 reference 到飞书

- 合并条件:CI 绿 + `zhengxuyu` approve + 提 issue 的人确认(若被 @)。
- 合并后 issue 自动 close(靠 `Closes #`)。
- **把 issue / PR 链接贴到飞书**对应的项目记录里(见 [飞书 reference](#飞书-reference)),让非 GitHub 的人也能追溯"这个问题谁在什么时候怎么解决的"。

---

## 谁负责什么

> 让 issue 的 "@ 谁" 落到真人,而不是空话。按当前贡献情况(可随团队调整):

| 范围 | 负责人 | 说明 |
|---|---|---|
| **合并 / 最终 review / 仲裁** | `@zhengxuyu` | org admin,所有 PR 的 auto-assigned reviewer,事实上的合并人 |
| **Stage 2 文献调研 / Literature Survey / 引用真实性** | `@BonnieZbw` (bowen) | literature_surveyor、citation 验证 |
| **Stage 6 实验执行 / infra / run_id** | `@YihangChen9` | Stage 6 长实验收集(#107) |
| **Stage 8 论文生成** | `@WuizaKaseiyo` | Stage 8 分块派发(#104) |
| **Pipeline 引擎 / 卡死恢复 / vessel** | `@KylJin` | completion-consumer 解卡(#105、#103) |
| **Eval / 质检 / cspaper** | `@haoyu-zhao` | per-stage eval agent(#108) |
| **Memento 记忆 / 配置** | `@martinei1` | combo_v2 ablation(#90) |
| **aigraph / LCG / 前端星球图 / eval 框架** | `@iamlilAJ` | aigraph 集成、orbit graph、evaluation 设计 |

> 不确定 @ 谁就 @ `@zhengxuyu` 让他 triage 转派。

---

## Agent PR 的额外要求

> 这条专治"改了 agent 行为却只有 mock 单测"的老问题。详见 [`AGENT_EVALUATION_DESIGN.md`](AGENT_EVALUATION_DESIGN.md)。

改动**任何 stage 的 producer / critic / skill / 引擎 dispatch 逻辑**的 PR,除了单测,还必须在 PR 描述里回答:

- **改的是哪个 stage 的行为?** 对应它的确定性指标(grounding_rate / run_id_verified / pdf_compiles …)是 **升了还是没退化**?
- **怎么验证的?** 至少跑过一次该 stage 的真实回放(不能只有 mock)。理想情况贴 before/after 指标。
- mock 单测**只证明代码不崩,不证明 agent 变好** —— 两者都要有。

（Agent PR evaluation pipeline 的完整分层设计在 `AGENT_EVALUATION_DESIGN.md` 的 Tier B。）

---

## 飞书 reference

每个 issue / PR 合并后,把 GitHub 链接同步到飞书,保证非 GitHub 视角也能追溯:

- **填飞书链接**:把对应飞书文档 / 多维表格的 URL 填到下面 `<飞书项目看板 URL>`,本节作为唯一入口。
- **记录什么**:`issue 链接 · PR 链接 · 负责人 · 一句话结论(怎么解决的)`。
- **谁来记**:PR 合并人(或负责人)在合并后顺手贴。

> 飞书看板:https://my.feishu.cn/wiki/Eo1nw7C5qia80ukusstcGMIMn5f?table=tblWCxOB6y8MBuVx&view=vewXxBNTOK
>
> 飞书记录格式(每行一条):
> `#<issue> 标题 | 负责人 @xxx | PR #<pr> | 状态 | 一句话结论`

---

## TL;DR(贴在群里那版)

1. **先开 issue**(用模板、描述清楚、@ 人、打 label)—— 没 issue 不开 PR。
2. **负责人开 PR**,正文 `Closes #issue`,@ 提 issue 的人一起 review;改 agent 行为的要证明指标没退化,不能只有 mock。
3. **合并后**把 issue + PR 链接贴到**飞书**。

*配套:代码风格看 `vibe-coding-guide.md`;agent/PR 的评估标准看 `AGENT_EVALUATION_DESIGN.md`。*
