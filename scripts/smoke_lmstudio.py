from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from app.providers.llm.lmstudio import LMStudioProvider
from app.providers.llm.smoke import smoke_tool_provider


async def smoke_lmstudio(
    *,
    base_url: str,
    model: str,
    timeout: float,
    max_tokens: int,
) -> dict[str, Any]:
    provider = LMStudioProvider(
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
    parser = argparse.ArgumentParser(description="Smoke-test a real LM Studio server")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1"),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("LMSTUDIO_MODEL", "google/gemma-4-e4b"),
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-output-tokens", type=int, default=128)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = asyncio.run(
            smoke_lmstudio(
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
                    "event": "lmstudio_smoke_failed",
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
