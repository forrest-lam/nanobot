# 修复内容重复问题

## 问题分析

根据日志 `835cd888_20260317_101843_686641.json` 的分析:

1. **总计11次tool call调用**
2. **严重的内容重复**:多处出现 2x, 4x, 8x, 甚至16x的重复
3. **最严重的重复**:第216行出现了4次完全相同的文件分享分析结果

## 根本原因

在 `nanobot/agent/loop.py` 的 `_run_agent_loop_stream` 函数中发现了一个bug:

```python
# 旧代码 (第492-493行)
async for chunk in stream:
    if chunk.content:
        accumulated_content += chunk.content
        final_content_parts.append(chunk.content)  # ❌ 未使用的累积变量
        yield (chunk.content, False, messages, None)
```

**问题**: `final_content_parts` 变量累积了所有chunk但从未被使用,这可能导致某些内部状态异常。

## 修复方案

### 1. 移除未使用的变量

**文件**: `nanobot/agent/loop.py`  
**行号**: 456-457, 492-493

```python
# 移除:
final_content_parts = []
final_content_parts.append(chunk.content)

# 保留:
accumulated_content += chunk.content
yield (chunk.content, False, messages, None)
```

### 2. 添加调试日志

为了更好地追踪问题,添加了以下调试日志:

- **迭代计数**: 记录当前在第几次迭代
- **Chunk计数**: 记录每次迭代yield了多少chunk
- **总chunk数**: 追踪整个会话总共yield了多少chunk
- **工具执行追踪**: 记录工具执行后是否会继续迭代

**关键日志点**:

```python
logger.debug("Stream iteration {}/{}", iteration, self.max_iterations)
logger.debug("Iter {}: Yielding chunk #{} ({} chars), total={}", 
            iteration, chunk_count, len(chunk.content), total_chunks_yielded)
logger.debug("Iter {}: Tools executed, continuing to next iteration", iteration)
logger.debug("Iter {}: Final response, {} chunks total", iteration, total_chunks_yielded)
```

### 3. 验证流程

修复后的流程:

1. **正常响应**:
   - 单次迭代 → stream chunks → yield all → return
   - 不会重复内容

2. **Tool使用场景**:
   - 迭代1: stream chunks → tool calls → execute tools
   - 迭代2: stream new chunks → final response
   - 每次迭代的chunks是独立的,不会重复

3. **Client tools场景**:
   - stream chunks → detect client tools → yield tool_calls_info → return
   - 立即return,不继续迭代

## 潜在原因分析

虽然移除了`final_content_parts`,但日志显示的重复模式(2x, 4x, 8x)表明可能还有其他因素:

### 可能的原因:

1. **LLM Provider本身的bug**
   - Provider的`chat_stream`可能返回了重复的chunks
   - 需要通过新日志确认

2. **多轮迭代导致**
   - 如果tool execution后,LLM重新生成了之前的回答
   - 新日志会显示iteration数量

3. **并发/异步问题**
   - 虽然不太可能,但异步generator可能有竞态条件
   - 日志会帮助排除此可能性

## 下一步

1. **运行测试**:使用相同的查询再次测试,观察新日志
2. **检查日志**:查看:
   - 是否还有重复(日志中的DUPLICATE标记)
   - 迭代次数是否正常
   - Chunk计数是否符合预期
3. **进一步修复**:如果问题依然存在,根据新日志定位精确原因

## 相关文件

- `nanobot/agent/loop.py`: 主要修复文件
- `nanobot/qqchat_compat/routes.py`: 调用流式输出的地方
- `nanobot/providers/litellm_provider.py`: LLM provider的stream实现

## 检测工具

日志中已经包含重复检测逻辑(284-325行):

- **方法1**: 检测按双换行符分割的段落重复
- **方法2**: 检测整体内容的倍数重复(2x, 3x, 4x等)

标记格式: `[DETECTED Nx DUPLICATE - removed in log]`
