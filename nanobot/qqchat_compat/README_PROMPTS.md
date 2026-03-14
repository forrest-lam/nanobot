# 用户级别 Prompt 管理系统

## 📝 概述

QQ Chat 兼容层现在支持**按 UIN 隔离的 Prompt 存储**,每个用户拥有独立的:
- `SOUL.md` - 人格设定(可追加自定义偏好)
- `TOOLS.md` - 工具使用偏好
- `USER.md` - 用户资料

## 🗂️ 目录结构

```
workspace/qqchat_compat/
  ├── memory/           # 现有: 每用户一个 JSON 文件
  └── prompts/          # 新增: 每用户一组 Prompt 文件
      ├── {uin}/
      │   ├── SOUL.md   # 基于默认模板,可追加用户自定义人设
      │   ├── TOOLS.md  # 用户工具偏好
      │   └── USER.md   # 用户资料
```

## 🚀 核心特性

### 1. 自动初始化
首次访问时,系统会自动从 `nanobot/templates/` 复制默认模板作为基础。

### 2. 人设追加
用户可以通过 API 逐步追加人格特征,系统会自动在 `SOUL.md` 末尾添加 `## 用户自定义偏好` 章节。

### 3. 完全隔离
不同 UIN 的 Prompt 文件完全独立,互不影响。

## 📡 API 端点

### 1. 获取用户所有 Prompt
```http
GET /prompt/{user_uin}
```

**响应示例:**
```json
{
  "user_uin": "123456",
  "prompts": {
    "SOUL.md": "# Soul\n\n我是你的 QQ 智能助手...",
    "TOOLS.md": "# Tools Preferences\n\n...",
    "USER.md": "# User Profile\n\n..."
  }
}
```

### 2. 更新指定 Prompt
```http
POST /prompt/{user_uin}/{prompt_name}
Content-Type: application/json

{
  "content": "新增内容",
  "append": true  // true=追加, false=覆盖
}
```

**示例:**
```bash
curl -X POST "http://localhost:8000/prompt/123456/SOUL.md" \
  -H "Content-Type: application/json" \
  -d '{"content": "## 额外说明\n\n喜欢用表情符号", "append": true}'
```

### 3. 添加人格特征(快捷方式)
```http
POST /prompt/{user_uin}/personality
Content-Type: application/json

{
  "trait": "喜欢简洁的回答"
}
```

这会自动追加到 `SOUL.md` 的 `## 用户自定义偏好` 章节:
```markdown
## 用户自定义偏好

- 喜欢简洁的回答
```

### 4. 重置为默认
```http
DELETE /prompt/{user_uin}
```

删除该用户的所有自定义 Prompt,下次访问时会重新从模板创建。

## 🔧 代码集成

### 在 `planner.py` 中使用

```python
def summarize_results(
    self,
    query: str,
    search_results: list[dict],
    user_uin: str | None = None,
) -> str:
    # ... 原有逻辑 ...
    
    # 读取用户的 SOUL.md
    if user_uin and self.prompt_store:
        soul = self.prompt_store.get_prompt(user_uin, "SOUL.md")
        if "用户自定义偏好" in soul:
            # 根据用户偏好调整输出
            ...
```

### 在 `routes.py` 中注入

```python
final_answer = planner.summarize_results(
    session.query,
    session.search_results,
    user_uin=session.user_uin,  # 传入 UIN 以启用个性化
)
```

## 📦 核心类: `UserPromptStore`

### 初始化
```python
from pathlib import Path
from nanobot.qqchat_compat.prompt_store import UserPromptStore

templates_dir = Path("nanobot/templates")
store = UserPromptStore(workspace, templates_dir)
```

### 主要方法

#### `get_prompt(user_uin, prompt_name)`
获取指定 Prompt 文件内容。

#### `get_all_prompts(user_uin)`
获取用户的所有 Prompt 文件。

#### `update_prompt(user_uin, prompt_name, content, append=False)`
更新或追加内容。

#### `append_personality(user_uin, trait)`
快捷方法:添加人格特征到 `SOUL.md`。

#### `reset_prompt(user_uin, prompt_name)`
重置单个文件为默认模板。

#### `delete_user_prompts(user_uin)`
删除用户的所有自定义 Prompt。

## 🎯 使用场景

### 场景 1: 用户要求简洁回答
```
用户: "以后回答简洁点,不要那么啰嗦"
AI: [调用 POST /prompt/{uin}/personality]
    {"trait": "偏好简洁的回答,避免冗长解释"}
```

之后该用户的查询会自动应用这个偏好。

### 场景 2: 用户定制搜索范围
```
用户: "默认只搜索最近7天的消息"
AI: [更新 TOOLS.md]
    {"content": "### Message Search\n- **Time Range**: last 7 days", ...}
```

### 场景 3: 用户资料补充
```
用户: "我在深圳工作,主要做前端开发"
AI: [更新 USER.md]
    {"content": "- **Location**: 深圳\n- **Role**: 前端开发", ...}
```

## 🔒 隔离性保证

1. **不同用户完全隔离**: 用户 A 的 Prompt 不会影响用户 B
2. **与主系统隔离**: QQChat Compat 的 Prompt 存储在 `qqchat_compat/prompts/`,不影响主 nanobot 的 `memory/` 或 `workspace/`
3. **会话级别隔离**: 每个会话只能访问对应 UIN 的 Prompt

## 📋 待优化

- [ ] 支持 Prompt 版本管理
- [ ] 增加 Prompt 导入/导出功能
- [ ] 添加 Prompt diff 预览
- [ ] 支持多语言 Prompt 模板
- [ ] 增加 Prompt 生效记录日志

## 🧪 测试

(由于环境缺少依赖,暂时无法运行单元测试,但代码逻辑已完成)

基本流程测试:
1. 创建用户 → 自动生成 3 个 Prompt 文件
2. 追加人格 → `SOUL.md` 新增自定义章节
3. 更新偏好 → 指定文件内容追加
4. 用户隔离 → 不同 UIN 互不干扰
5. 重置功能 → 恢复默认模板

---

**作者**: Claude Assistant  
**日期**: 2026-03-14  
**版本**: 1.0.0
