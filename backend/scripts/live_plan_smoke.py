from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from migraai.planning.context import build_context
from migraai.planning.devin_client import DevinClient, DevinV1Client
from migraai.planning.prompt import build_prompt
from migraai.planning.schema import SCHEMA, Plan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kb-db", type=Path, required=True)
    parser.add_argument("--source", default="teradata")
    parser.add_argument("--target", default="snowflake")
    parser.add_argument("--acu-cap", type=int, default=10)
    args = parser.parse_args()
    pat = os.environ["MIGRAAI_DEVIN_PAT"]
    org_id = os.environ.get("MIGRAAI_DEVIN_ORG_ID", "")
    base_url = os.environ.get("MIGRAAI_DEVIN_API_BASE_URL", "https://api.devinenterprise.com")
    if pat.startswith("apk_"):
        client = DevinV1Client(base_url, pat)
        api_version = "v1"
    elif pat.startswith("cog_") and org_id:
        client = DevinClient(base_url, org_id, pat)
        api_version = "v3"
    else:
        raise SystemExit("Provide an apk_ key, or a cog_ key and MIGRAAI_DEVIN_ORG_ID.")
    started = time.monotonic()
    context_started = time.monotonic()
    context = build_context(args.source, args.target, args.kb_db)
    context_s = time.monotonic() - context_started
    try:
        print(f"api_version={api_version}")
        upload_started = time.monotonic()
        attachment = client.upload_attachment("migration-context.md", context.markdown.encode())
        upload_s = time.monotonic() - upload_started
        prompt = build_prompt(args.source, args.target, attachment)
        create_started = time.monotonic()
        session_id, session_url = client.create_session(
            prompt,
            schema=SCHEMA,
            max_acu_limit=args.acu_cap,
            title=f"{args.source} to {args.target} migration plan",
            tags=["migraai", "migration-plan"],
        )
        create_s = time.monotonic() - create_started
        print(f"session_id={session_id}")
        print(f"session_url={session_url}")
        poll_started = time.monotonic()
        blocked_polls = 0
        while True:
            result = client.get_session(session_id)
            status = result.get("state")
            if status in {"done", "failed"} or (
                status == "blocked" and result.get("structured_output") is not None
            ):
                break
            if status == "blocked":
                blocked_polls += 1
                if blocked_polls >= 2:
                    break
            else:
                blocked_polls = 0
            time.sleep(15)
        poll_s = time.monotonic() - poll_started
        output = result.get("structured_output")
        valid = False
        if output is not None:
            try:
                Plan.model_validate(output)
                valid = True
            except Exception:
                pass
        print(f"elapsed_s={time.monotonic() - started:.1f}")
        print(
            f"timings_s=context:{context_s:.1f},upload:{upload_s:.1f},"
            f"create:{create_s:.1f},poll:{poll_s:.1f}"
        )
        print(f"status={status}")
        for key in ("acu_count", "acu_limit", "max_acu_limit"):
            if key in result:
                print(f"{key}={result[key]}")
        print(f"output_size={len(str(output).encode('utf-8')) if output is not None else 0}")
        print(f"validation={'passed' if valid else 'failed'}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
