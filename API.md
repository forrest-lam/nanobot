# 流式输出问题诊断和解决方案

## 问题现象
客户端几乎同时收到所有SSE chunk,而不是真正的流式接收。

## 根本原因

### 1. API网关缓冲 (最可能)
使用的腾讯云API网关 `https://api.lkeap.cloud.tencent.com/v1` 可能在缓冲所有流式响应。

**诊断方法:**
- 检查服务器日志中chunk产生的时间戳
- 检查客户端收到chunk的时间戳
- 如果服务器端逐步产生,但客户端同时收到,则确认是网关缓冲

**解决方案:**
1. 直接连接到 DeepSeek 官方 API (不经过腾讯云网关)
2. 配置腾讯云网关禁用缓冲 (需联系腾讯云支持)
3. 使用其他支持真正流式输出的 provider

### 2. LiteLLM provider 配置问题
某些 LiteLLM provider 可能不支持真正的流式输出。

**验证方法:**
```bash
python test_stream.py
```

查看每个chunk之间的时间间隔。如果间隔很小(<10ms),说明不是真正的流式。

### 3. FastAPI/Uvicorn 缓冲
FastAPI 的 StreamingResponse 可能有内部缓冲。

**已应用的解决方案:**
- 在每个 yield 后添加 `await asyncio.sleep(0)` 让出事件循环控制权
- 这确保每个chunk都能立即被发送

## 代码改动总结

### 1. nanobot/agent/loop.py
添加了日志追踪每个chunk的产生时间:
```python
logger.debug("Streaming chunk: {} chars", len(chunk.content))
```

### 2. nanobot/qqchat_compat/routes.py
- 添加了日志追踪SSE事件发送
- 添加了 `await asyncio.sleep(0)` 确保立即发送

### 3. nanobot/providers/litellm_provider.py
添加了时间戳日志追踪 LiteLLM chunk 接收时间

## 建议的测试步骤

### 步骤1: 验证服务器端流式输出
1. 重启 nanobot 服务
2. 发送测试请求
3. 查看服务器日志,确认chunk是逐步产生的:
```
[DEBUG] Received chunk from LiteLLM at 1710579253.797
[DEBUG] Streaming chunk: 5 chars
[DEBUG] Yielding SSE chunk: 5 chars
[DEBUG] Received chunk from LiteLLM at 1710579253.850  <- 注意时间差
[DEBUG] Streaming chunk: 8 chars
[DEBUG] Yielding SSE chunk: 8 chars
```

如果时间戳都相同或非常接近(<10ms),说明 LiteLLM/API 本身就没有真正流式输出。

### 步骤2: 测试直连 DeepSeek API
修改配置文件 `~/.nanobot/config.json`:
```json
{
  "providers": {
    "deepseek": {
      "apiKey": "你的DeepSeek官方API Key",
      "apiBase": "https://api.deepseek.com",  // 使用官方API
      "extraHeaders": null
    }
  }
}
```

重启服务并测试,如果流式正常,则确认是腾讯云网关问题。

### 步骤3: 尝试其他 provider
临时切换到 Anthropic Claude 或 OpenAI 测试:
```json
{
  "agents": {
    "defaults": {
      "model": "anthropic/claude-sonnet-4-5",
      "provider": "anthropic"
    }
  },
  "providers": {
    "anthropic": {
      "apiKey": "你的Anthropic API Key",
      "apiBase": null,
      "extraHeaders": null
    }
  }
}
```

如果其他 provider 流式正常,则确认是 DeepSeek/腾讯云网关的问题。

## 容错机制说明

当前实现已支持流式失败时自动回退到 Tavily 搜索,确保功能可用性。

## 后续优化方向

1. **添加 provider 能力检测** - 自动检测是否支持真正的流式输出
2. **添加配置选项** - 允许强制使用非流式模式
3. **优化chunk大小** - 调整chunk大小以平衡响应速度和网络开销
4. **添加客户端重连机制** - 处理SSE连接中断的情况
