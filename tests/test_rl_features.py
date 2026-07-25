from __future__ import annotations

import pytest
from nimmakai.routing.rl_features import extract_feature_vector, FEATURE_DIM, FEATURE_NAMES


def test_extract_feature_vector_empty():
    x = extract_feature_vector({})
    assert len(x) == FEATURE_DIM
    assert all(0.0 <= v <= 1.0 for v in x)


def test_extract_feature_vector_coding_agent():
    body = {
        "messages": [
            {"role": "user", "content": "def fib(n):\n    return n if n <= 1 else fib(n-1) + fib(n-2)\n```"}
        ],
        "tools": [{"type": "function", "function": {"name": "read_file"}}]
    }
    headers = {"User-Agent": "Cursor/0.45.0"}
    x = extract_feature_vector(body, headers=headers, intent_name="coding_agentic")
    
    assert len(x) == FEATURE_DIM
    # tool density > 0
    assert x[1] == 0.1
    # code syntax ratio high
    assert x[2] == 1.0
    # python detected
    assert x[3] == 1.0
    # agent harness detected
    assert x[7] == 1.0
    # intent prior coding
    assert x[11] == 1.0


def test_extract_feature_vector_reasoning_multimodal():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Please prove the mathematical theorem step by step."},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
                ]
            }
        ]
    }
    x = extract_feature_vector(body, intent_name="reasoning")
    assert x[8] == 1.0  # image modality
    assert x[9] == 1.0  # reasoning keywords
