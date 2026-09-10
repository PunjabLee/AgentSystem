# 开发约定

## 分支策略（P1.1.3）

| 分支 | 用途 | 合入方式 |
|---|---|---|
| `main` | 唯一可发布分支。**受保护**：不直接提交 | 仅从下列分支合入 |
| `feat/p{N}-*` | 阶段性功能开发，`{N}` 是 WBS 阶段号 | 阶段出口判据 PASS 后合入 |
| `docs/*` | 纯文档与设计变更 | 评审通过后合入 |
| `fix/*` | 缺陷修复 | 复现用例转绿后合入 |

单人项目为什么还要分支：**出口判据需要一个可回退的锚点**。`main` 上永远是
判据通过过的状态，`feat/*` 上是尚未判定的状态。没有这条边界，"P1 完成了吗"
就退化成翻提交记录。

`main` 保护当前靠约定 —— 仓库尚无远程，分支保护规则要等推到 GitHub 后在
仓库设置里配。推送后须启用：禁止直接推送、要求 CI 通过。

### 合入前必须通过

```bash
make check          # ruff + format + pytest
make p1-exit        # 阶段出口判据（P2 起换成对应阶段的判据）
```

## 提交信息

```
<type>(<scope>): <一句话说清改了什么>

正文写**为什么**，不写改了哪些文件 —— 后者 git diff 已经说了。
涉及实测的，把数字写进来：证据比结论有用。
```

`type` 取 `feat` / `fix` / `docs` / `refactor` / `test` / `chore`。
`scope` 用 WBS 阶段号（`p1`、`p2`……）。

## 代码规则（宪法第十一条）

**所有代码必须自带注释。** 这不是风格偏好，是宪法条款，且有机械保障：

- `ruff` 开了 `pydocstyle`（D）规则集，缺 docstring 直接红
- `# noqa: D` 必须**同行给出理由**，`tests/test_constitution_checks.py`
  有一条断言守着 —— 否则这个豁免会变成"关掉检查"的静默开关

注释写**为什么这样做**，不写代码在做什么。「这里用 UPDATE ... WHERE state='pending'
做原子消费」是废话，「先查后写在并发下必然有窗口」才是注释。

## 数据库变更

普通迁移走 `make migrate`。**改 `audit_log` 结构必须走 `make migrate-audit`** ——
事件触发器会拦下一切对它的 `ALTER`，包括合法迁移。这是刻意的：审计表的结构
变更就该是需要显式解锁的动作。

---

## main 分支保护（2026-09-10 起生效）

远程已开启保护，**直接 `git push origin main` 会被拒**：

```
remote: - Required status check "test" is expected.
! [remote rejected] main -> main (protected branch hook declined)
```

| 规则 | 值 | 为什么 |
|---|---|---|
| 必过检查 | `test` | 本地 `make check` 绿 ≠ CI 绿。CI 曾红了四天没人发现，本地环境的残留状态掩盖了问题 |
| 严格模式 | 开 | 分支须与 main 同步后才能合，避免「各自都绿、合起来红」 |
| PR 审批 | **不要求** | 单人项目里自批是形式主义，徒增两步 |
| 管理员豁免 | **不豁免** | 唯一的开发者就是管理员；豁免等于这条规则对本项目完全不生效 |
| 强推 / 删除 | 禁止 | main 是判据通过过的锚点，不该被重写 |

### 日常流程

```bash
git checkout -b feat/xxx          # 从 main 起分支
# ... 改动 ...
make check                        # 本地闸门
git push -u origin feat/xxx       # CI 自动跑
gh pr create --fill               # 无需审批，等 CI 绿
gh pr merge --squash --delete-branch
```

紧急情况下解除保护：`gh api -X DELETE repos/PunjabLee/AgentSystem/branches/main/protection`
—— 解除后**务必恢复**，否则闸门就永久失效了。
