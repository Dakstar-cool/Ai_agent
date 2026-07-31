from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from app.providers.llm.ollama import OllamaProvider
from app.providers.llm.smoke import smoke_tool_provider


async def smoke_ollama(
    *,
    base_url: str,
    model: str,
    timeout: float,
    max_tokens: int,
) -> dict[str, Any]:
    provider = OllamaProvider(
        base_url=base_url,
        model=model,
        timeout=timeout,
        max_output_tokens=max_tokens,
    )
    try:
        return await smoke_tool_provider(provider, max_tokens=max_tokens)
    finally:
        await provider.aclose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test a real Ollama server")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OLLAMA_MODEL"),
        required="OLLAMA_MODEL" not in os.environ,
        help="Loaded tool-capable model name (or set OLLAMA_MODEL)",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = asyncio.run(
            smoke_ollama(
                base_url=args.base_url,
                model=args.model,
                timeout=args.timeout,
                max_tokens=args.max_output_tokens,
            )
        )
    except Exception as exc:  # noqa: BLE001 - CLI must not expose provider internals
        print(
            json.dumps(
                {
                    "event": "ollama_smoke_failed",
                    "error_type": exc.__class__.__name__,
                },
                separators=(",", ":"),
            )
        )
        return 1

    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
