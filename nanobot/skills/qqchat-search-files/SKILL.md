---
name: qqchat-search-files
description: QQChat 文件搜索指南。当用户需要查找文件、搜索文档、下载的资料、查看文件内容时使用此技能。触发关键词：找文件、搜索文档、下载的、PDF、Word、Excel、PPT、图片、视频、压缩包、会议资料、合同、报表、文档内容。
channel: qqchat_http
metadata: '{"nanobot":{"compatTools":["search_files","get_file_chunks","search_messages","get_recent_chats","get_recent_messages","search_chats"]}}'
---

# 搜索文件指南

本文档说明如何使用 QQChat MCP 工具搜索和查看文件。

## 🎯 使用场景

**核心意图**：用户想要 **查找文件** 或 **查看文件内容**

**典型问题示例**：
- "找一下会议纪要" / "搜索合同文件"
- "昨天下载的那个PDF在哪"
- "谁发过项目计划文档"
- "找一下包含'季度报告'的文件"
- "帮我看看合同里写了什么"
- "这个PPT讲了什么内容"

## 优先级说明

- **高优先级方案（priority=3）**：最合适的方案，能直接查到数据，一步到位
- **中优先级方案（priority=2）**：可能需要中间步骤，查询结果需要进一步处理
- **低优先级方案（priority=1）**：兜底方案，需要多步处理，范围较大需要筛选

---

## 🟠 按关键词查找文件

**用户问**："找一下会议纪要" / "搜索合同文件" / "谁发过项目计划"

### 高优先级方案（priority=3）

**使用 search_files 直接搜索**

调用方式：使用search_files工具，传入keyword参数

返回结果说明：
- 返回匹配的文件列表，每个文件包含完整信息
- fileId：文件唯一标识（重要！用于后续获取文件内容）
- fileName：文件名
- fileSize：文件大小（字节）
- senderName：发送者名称
- peerName：会话名称（群名/好友名）
- msgTime：消息时间（Unix时间戳）
- file_cards：已格式化的 `<file_card>` 标签内容

**🚨🚨🚨 强制输出格式 🚨🚨🚨**

调用此工具后，你的回复中必须包含 `<file_card>` 标签！
返回结果的 file_cards 字段已经是格式化好的 `<file_card>` 标签，直接复制到回复中！

**错误示例**：
```
找到文件：
1. 会议纪要.pdf
2. 项目计划.docx
```

**正确示例**：
```
找到2个文件：

<file_card>{...}</file_card>
<file_card>{...}</file_card>
```

**参数说明**：
- keyword：搜索关键词（必填）
- fileType：文件类型筛选
  - 0：全部（默认）
  - 1：文档（.doc, .docx, .txt等）
  - 2：PDF
  - 3：表格（.xls, .xlsx等）
  - 4：PPT（.ppt, .pptx等）
  - 5：图片视频（.jpg, .png, .mp4等）
  - 6：压缩包（.zip, .rar等）
- enableContentSearch：是否启用内容搜索（默认true，搜索文件名和内容）
- chatType：限定会话类型（1=私聊，2=群聊，不传则全部）
- peerUin：限定特定会话的UIN（不传则全部）
- pageLimit：返回数量限制（默认50，最大100）

**优势**：
- 一步到位，直接获取文件列表
- 支持文件名搜索和文件内容搜索（如PDF、Word文档内的文字）
- 可按文件类型精确筛选
- 可限定特定会话或会话类型
- 返回完整文件信息，包括发送者、时间、大小等

**适用场景**：
- 按文件名查找文件
- 按文件内容查找文件（如"包含'季度报告'的文档"）
- 筛选特定类型的文件（如"所有PDF文件"）
- 查找特定人发送的文件
- 查找特定群/会话中的文件

### 中优先级方案（priority=2）

**通过 search_messages 搜索聊天记录上下文，间接定位文件**

**适用场景**：
- 用户记不起文件的任何信息（内容、标题、发送人、所在群等）
- 用户只记得与文件同时出现的聊天记录上下文
- 需要通过聊天记录的关键词来推断文件位置

**执行步骤**：
1. 使用 search_messages 搜索用户提到的聊天记录关键词
2. 从返回的消息中提取包含文件的消息（检查消息中的 fileId 字段）
3. 使用 get_file_chunks 获取文件详细信息和内容

**优势**：
- search_messages 返回的消息包含 fileId，可获取完整文件信息
- 可通过聊天上下文间接定位文件
- 结合 get_file_chunks 可获取文件内容和元数据

**示例**：
用户："找一下我们讨论季度 OKR 时发的那个表格"
→ search_messages(keywords=["季度", "OKR"]) 
→ 从消息中提取 fileId
→ get_file_chunks(fileId) 获取表格详情

**适用场景**：
- search_files不可用时
- 只需要知道谁发过某个文件，不需要下载

### 低优先级方案（priority=1）

**通过 get_recent_chats + get_recent_messages 逐个会话遍历查找**

**适用场景**：
- search_files 和 search_messages 都不可用时
- 需要在特定几个会话中查找文件
- 用户明确指定了会话范围（如"看看最近聊天里的文件"）

**执行步骤**：
1. 调用 get_recent_chats 获取最近会话列表
2. 逐个调用 get_recent_messages 获取每个会话的消息
3. 从消息中筛选包含 fileId 的消息（文件消息）
4. 使用 get_file_chunks 获取文件详细信息和内容

**劣势**：
- 需要大量工具调用，性能开销大
- 只能搜索最近会话，不活跃会话的文件找不到
- 需要遍历多个会话，效率较低

**示例**：
用户："看看最近聊天里有哪些 PDF 文件"
→ get_recent_chats(limit=20) 获取最近20个会话
→ 遍历每个会话：get_recent_messages(uids=[uid])
→ 从消息中提取 fileId
→ get_file_chunks(fileId) 获取文件信息，筛选出 PDF 类型

---

## 🔵 查看文件详细内容

**用户问**："帮我看看合同里写了什么" / "这个PPT讲了什么" / "文档第3页是什么内容"

### 高优先级方案（priority=3）

**使用 search_files + get_file_chunks 获取文件内容**

**步骤1：搜索文件**
- 使用search_files找到目标文件
- 获取文件的fileId（重要！）

**步骤2：获取文件内容**
- 使用get_file_chunks，传入fileId和fileName
- 返回文件的分块（chunk）内容

**调用方式**：
```
get_file_chunks({
  fileId: "abc123",          // 推荐，来自search_files结果
  fileName: "合同.pdf"        // 必填
})
```

**返回结果说明**：
- chunkCount：chunk总数
- chunkList：chunk列表，每个chunk包含：
  - index：索引（从0开始）
  - content：文本内容
  - pageNumber：页码（如果是PDF等有页码的文档）

**优势**：
- 获取完整的文件文本内容
- 按chunk分块，便于分析和定位
- 支持页码定位（PDF等格式）
- 可用于深度分析、摘要、问答等

**适用场景**：
- 需要查看文件完整内容
- 需要分析文件内容（如提取关键信息、生成摘要）
- 需要回答关于文件内容的问题
- 需要定位文件中的特定段落或页面

### 中优先级方案（priority=2）

**仅通过 search_files 搜索，使用文件元信息推断**

步骤1：使用search_files找到文件
步骤2：根据文件名、发送者、时间等元信息进行推断
步骤3：如果无法回答，建议用户打开文件查看

**劣势**：
- 无法获取文件实际内容
- 只能根据文件名推断可能的内容
- 无法进行深度分析

**适用场景**：
- 用户只需要知道文件的基本信息
- 文件名已经包含足够的信息
- get_file_chunks不可用时

### 低优先级方案（priority=1）

**无法获取内容，建议用户自行打开查看**

**适用场景**：
- 所有工具都不可用时
- 文件类型不支持内容提取（如某些加密文件）

---

## 高级技巧

### 使用 fileId 精确定位文件

**说明**：
- fileId是文件的唯一标识，强烈建议使用
- 先用search_files获取fileId，再用get_file_chunks获取内容
- 使用fileId可以避免同名文件的混淆

**示例**：
```
用户问："帮我看看张三发的合同"
→ search_files({ keyword: "合同", enableContentSearch: false })
→ 从结果中找到张三发送的合同，获取fileId
→ get_file_chunks({ fileId: "abc123", fileName: "合同.pdf" })
→ 分析chunk内容，回答用户问题
```

### 组合条件精确筛选

**用户问**："找一下上周技术群里的所有PDF文件"

**最优方案**：
- 步骤1：使用search_chats找到"技术群"，获取群的peerUin
- 步骤2：计算上周的时间范围（但search_files不支持时间筛选）
- 步骤3：使用search_files，传入fileType=2（PDF）、chatType=2（群聊）、peerUin（技术群的UIN）
- 步骤4：从返回结果中筛选msgTime在上周范围内的文件

### 内容搜索 vs 文件名搜索

**区别**：
- enableContentSearch=true（默认）：搜索文件名和文件内容（如Word、PDF中的文字）
- enableContentSearch=false：仅搜索文件名

**使用建议**：
- 如果用户说"找包含XX的文件"，使用内容搜索
- 如果用户说"找名为XX的文件"，关闭内容搜索以提高准确度

**示例**：
```
用户问："找包含'项目预算'的文档"
→ search_files({ keyword: "项目预算", enableContentSearch: true, fileType: 1 })
→ 返回文件名或内容中包含"项目预算"的所有文档

用户问："找名为'项目预算'的文件"
→ search_files({ keyword: "项目预算", enableContentSearch: false })
→ 返回文件名包含"项目预算"的文件
```

### 分页获取大量文件

**说明**：
- pageLimit默认50，最大100
- 如果结果很多，考虑分页或添加更多筛选条件

**示例**：
```
用户问："找所有PDF文件"
→ search_files({ keyword: "", fileType: 2, pageLimit: 100 })
→ 如果返回100个结果，提示用户可能有更多文件，建议添加关键词筛选
```

### 处理多个同名文件

**策略**：
- 根据发送者（senderName）区分
- 根据会话（peerName）区分
- 根据时间（msgTime）区分
- 根据文件大小（fileSize）区分

**示例**：
```
用户问："找张三发的会议纪要"
→ search_files({ keyword: "会议纪要" })
→ 从返回结果中筛选senderName为"张三"的文件
→ 如果有多个，按时间排序，返回最新的
```

---

## ⚠️ 注意事项

### 关于 file_card 标签

- **必须使用**：调用search_files后，必须在回复中包含 `<file_card>` 标签
- **直接复制**：返回结果的file_cards字段已经格式化好，直接复制即可
- **禁止自定义格式**：不要用列表、表格等其他格式展示文件信息

### 关于 fileId

- **强烈推荐使用**：调用get_file_chunks时，优先使用fileId
- **精确定位**：fileId可以精确定位文件，避免同名文件混淆
- **来源**：fileId来自search_files的返回结果

### 关于文件类型

- **部分文件无内容**：图片、视频、压缩包等文件可能没有chunk信息
- **加密文件**：加密的PDF、Word等文件可能无法提取内容
- **大文件**：chunk数量可能很多，注意处理性能

### 关于搜索范围

- **默认全局搜索**：不传chatType和peerUin时，搜索所有会话的文件
- **精确筛选**：如果用户明确指定群或好友，先获取peerUin再搜索
