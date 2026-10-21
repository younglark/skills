# skills

可复用的 Agent Skill 仓库（Cursor / Grok Bot 兼容的 `SKILL.md` 格式）。

由 [@skills](https://github.com/younglark) 助手维护：需要新建 skill 时说明用途与触发场景，助手按 [SPEC.md](./SPEC.md) 撰写并提交到本仓库。

## 结构

```
skills/                 # 已发布的 skill（每个目录一个）
  <skill-id>/
    SKILL.md            # 必需
    ...                 # 可选辅助文件
templates/
  example-skill/        # 空白模板示例
SPEC.md                 # 创建规范与最佳实践
```

## 怎么用

1. 打开对应 `skills/<skill-id>/SKILL.md`，按说明在助手侧安装或引用。
2. 需要新 skill：联系本仓库维护助手，说明「什么时候用」和「要完成什么」。
3. 提交前请对照 [SPEC.md](./SPEC.md) 自检。

## License

Apache-2.0（见 [LICENSE](./LICENSE)）
