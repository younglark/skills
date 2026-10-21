# Skill 创建规范

本文约定本仓库中 skill 的格式与最佳实践，与 Cursor / Grok Bot 的 `SKILL.md` 约定对齐。

## 1. 目录与命名

- 每个 skill 独占目录：`skills/<skill-id>/`
- `<skill-id>` 使用 **kebab-case**（小写 + 连字符），例如 `daily-standup`、`pr-review-checklist`
- 目录内至少包含 `SKILL.md`；可附带脚本、样例、参考文档，但入口必须是 `SKILL.md`

## 2. SKILL.md 格式

```markdown
---
name: Human readable name
description: >-
  一句话说清「何时使用」本 skill（必填；读者只靠它决定要不要跑）
---

# 标题

可执行的步骤与约束……
```

### frontmatter

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 展示名；可与目录名不同 |
| `description` | 是 | **何时使用**，不要写成长篇说明书 |

其他 Cursor 专有字段（如 `globs`、`alwaysApply`）若存在，保留；本仓库不要求填写。

### body

- 写 **通用、可复用** 的步骤，不要写死某个助手名、某个私有频道、某个私人仓库路径
- 具体账号 / 频道 / 仓库等属于「调用时上下文」，不写进共享模板
- 步骤要可执行：先做什么、成功标准是什么、失败时停在哪
- 需要用户确认的破坏性操作要写明

## 3. 最佳实践

1. **description 优先**：写「在……时使用」，而不是「这个 skill 可以……」
2. **一个 skill 一件事**：能拆成独立触发条件的，拆成多个 skill
3. **短而硬**：少讲原则，多写检查清单与命令/产出物
4. **不藏秘密**：不写 token、密码、内网地址
5. **可测试**：按步骤走一遍，外人也能得出同样结果
6. **中英文**：默认与需求方语言一致；面向公开仓库可用中英双语小节

## 4. 提交流程（维护助手）

1. 确认需求：触发场景、期望产出、边界
2. 按本规范起草 `skills/<skill-id>/SKILL.md`
3. 自检 description、通用性、无秘密
4. 提交到本仓库默认分支（或开 PR 后合并）
5. 回复需求方：skill 路径与一句话用法

## 5. 审核清单

- [ ] `skill-id` 为 kebab-case，且仓库内不冲突
- [ ] frontmatter 含 `name` 与 `description`
- [ ] `description` 写清何时使用
- [ ] 正文无助手专属硬编码、无密钥
- [ ] 步骤可独立执行，有明确完成条件
