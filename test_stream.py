#!/usr/bin/env python3
"""Test script for streaming response."""

import asyncio
import time
from nanobot.providers.litellm_provider import LiteLLMProvider


async def test_streaming():
    """Test streaming chat completion."""
    # Initialize provider (use your actual config)
    provider = LiteLLMProvider(
        api_key="your-api-key-here",  # Replace with actual key
        default_model="anthropic/claude-opus-4-5",
    )
    
    messages = [
        {"role": "user", "content": "给我写一首长诗,至少200字"}
    ]
    
    print("Testing streaming mode:")
    print("-" * 60)
    
    accumulated = ""
    start_time = time.time()
    chunk_count = 0
    
    async for chunk in provider.chat_stream(messages=messages):
        chunk_count += 1
        elapsed = time.time() - start_time
        
        if chunk.content:
            print(f"[{elapsed:.3f}s] Chunk {chunk_count}: {len(chunk.content)} chars", end="")
            print(f" - '{chunk.content[:20]}...'")
            accumulated += chunk.content
        
        if chunk.tool_calls:
            print(f"\n[{elapsed:.3f}s] Tool calls: {chunk.tool_calls}")
    
    total_time = time.time() - start_time
    print("\n" + "-" * 60)
    print(f"Total: {len(accumulated)} chars in {total_time:.3f}s")
    print(f"Total chunks: {chunk_count}")
    print(f"Average time per chunk: {total_time/chunk_count:.3f}s" if chunk_count > 0 else "N/A")


if __name__ == "__main__":
    asyncio.run(test_streaming())
