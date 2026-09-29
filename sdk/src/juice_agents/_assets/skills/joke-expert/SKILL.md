---
name: joke-expert
description: 提供示例笑话库，支持按分类获取随机笑话。
version: "0.1.0"
---

# Joke Expert

## 使用场景
- 需要快速返回轻量的中文笑话示例，便于测试对话或工具调用链。
- 想确认可用的笑话分类，或演示如何在技能中查阅本地文档。

## 如何使用
1. 查看分类（JSON 输出）：
   - `python agents/skills/joke-expert/scripts/list_joke_categories.py`
   - 示例输出：`{"categories": ["dad", "tech"], "count": 2}`
2. 获取笑话（JSON 输出）：
   - 全量随机：`python agents/skills/joke-expert/scripts/get_random_joke.py`
   - 指定分类：`python agents/skills/joke-expert/scripts/get_random_joke.py --category tech`
   - 示例输出：`{"joke": {"id": "tech-001", "category": "tech", "setup": "...", "punchline": "...", "text": "..."}}`
3. 自定义数据路径：上述命令增加 `--data-path /path/to/jokes.json`。

## 脚本列表
- `scripts/list_joke_categories.py`: 返回当前笑话库支持的分类列表。
- `scripts/get_random_joke.py`: 按需从指定分类或全量池中返回一条笑话。

## 数据来源
- 本技能内置的少量示例笑话，位于 `data/jokes.json`，仅用于测试与演示。
