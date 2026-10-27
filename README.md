# skillhub-batch-publish

> WorkBuddy Skill — `skillhub-batch-publish`

## 简介

批量将指定版本的 Skill（如 docs/dist 下 *-1.4.1 文件夹集合）发布/更新到 SkillHub 与 ClawHub。封装 Windows Git-Bash 安装踩坑、SKILL.md frontmatter 注入（服务端 name/description + 索引 slug/version/displayName/summary/license 共 7 字段必须齐备）、SemVer 三段式强制、dry-run 不校验服务端字段、服务端限流与 524 假失败自动重试、`/ap

## 安装

```bash
git clone https://github.com/funewa/skillhub-batch-publish.git
cp -r skillhub-batch-publish ~/.workbuddy/skills/skillhub-batch-publish
```

## 使用

在 WorkBuddy 中直接描述你的需求，Agent 会依据 `SKILL.md` 的 `description` 自动匹配本技能。详见 `SKILL.md`。

## 目录结构

```
├── SKILL.md      # 技能定义（frontmatter + 说明）
├── scripts/
└── README.md
```

---

Published from the `user`-level WorkBuddy skill library.
