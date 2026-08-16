#!/usr/bin/env python3
"""Team-facing CLI for managing LiteLLM gateway keys.

Wraps app/gateway/admin_client.py so nobody on the team needs to remember
raw curl incantations against the master key, or risk a typo in a budget
number that's hard to notice until it's already caused a problem.

Usage:
    python -m app.gateway.cli create --name alice-dev --budget 5.00 --rpm 20
    python -m app.gateway.cli list
    python -m app.gateway.cli info sk-abc123...
    python -m app.gateway.cli update sk-abc123... --budget 10.00
    python -m app.gateway.cli revoke sk-abc123...
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.gateway.admin_client import GatewayAdminClient


def _print_key_summary(k: dict) -> None:
    key = k.get("key") or k.get("key_name") or k.get("token", "?")
    alias = k.get("key_alias", "(no alias)")
    spend = k.get("spend", 0.0)
    budget = k.get("max_budget")
    rpm = k.get("rpm_limit")
    models = k.get("models", [])
    print(f"  {key}")
    print(f"    alias: {alias}  models: {models}")
    print(f"    spend: ${spend:.6f}  budget: {'$' + str(budget) if budget else 'unlimited'}  rpm_limit: {rpm or 'unlimited'}")


async def cmd_create(args: argparse.Namespace) -> None:
    client = GatewayAdminClient()
    result = await client.create_key(
        alias=args.name,
        models=args.models,
        max_budget=args.budget,
        budget_duration=args.budget_duration,
        rpm_limit=args.rpm,
    )
    print(f"Created key for '{args.name}':")
    print(f"  {result['key']}")
    print(f"  models={args.models} budget={args.budget or 'unlimited'} rpm_limit={args.rpm or 'unlimited'}")
    print()
    print("Store this key securely -- it will not be shown again by this command.")


async def cmd_list(args: argparse.Namespace) -> None:
    client = GatewayAdminClient()
    keys = await client.list_keys()
    if not keys:
        print("No keys found.")
        return
    print(f"{len(keys)} key(s):")
    for k in keys:
        _print_key_summary(k if isinstance(k, dict) else {"key": k})


async def cmd_info(args: argparse.Namespace) -> None:
    client = GatewayAdminClient()
    info = await client.get_key_info(args.key)
    detail = info.get("info", info)
    _print_key_summary({**detail, "key": args.key})


async def cmd_update(args: argparse.Namespace) -> None:
    client = GatewayAdminClient()
    result = await client.update_key(
        key=args.key, max_budget=args.budget, rpm_limit=args.rpm, models=args.models,
    )
    print(f"Updated {args.key}:")
    if args.budget is not None:
        print(f"  new budget: ${args.budget}")
    if args.rpm is not None:
        print(f"  new rpm_limit: {args.rpm}")
    if args.models is not None:
        print(f"  new models: {args.models}")


async def cmd_revoke(args: argparse.Namespace) -> None:
    if not args.yes:
        confirm = input(f"Revoke key {args.key}? This cannot be undone. [y/N] ")
        if confirm.lower() != "y":
            print("Aborted.")
            return
    client = GatewayAdminClient()
    await client.revoke_key(args.key)
    print(f"Revoked {args.key}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gateway_cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create a new virtual key")
    p_create.add_argument("--name", required=True, help="Human-readable alias, e.g. 'alice-dev'")
    p_create.add_argument("--models", nargs="+", default=["fast"], help="Allowed model aliases (default: fast)")
    p_create.add_argument("--budget", type=float, default=None, help="Max spend in USD")
    p_create.add_argument("--budget-duration", default="24h", help="Budget reset period, e.g. 24h, 30d")
    p_create.add_argument("--rpm", type=int, default=None, help="Requests-per-minute limit")
    p_create.set_defaults(func=cmd_create)

    p_list = sub.add_parser("list", help="List all keys")
    p_list.set_defaults(func=cmd_list)

    p_info = sub.add_parser("info", help="Show details for one key")
    p_info.add_argument("key", help="The sk-... key value")
    p_info.set_defaults(func=cmd_info)

    p_update = sub.add_parser("update", help="Update limits on an existing key")
    p_update.add_argument("key", help="The sk-... key value")
    p_update.add_argument("--budget", type=float, default=None)
    p_update.add_argument("--rpm", type=int, default=None)
    p_update.add_argument("--models", nargs="+", default=None)
    p_update.set_defaults(func=cmd_update)

    p_revoke = sub.add_parser("revoke", help="Revoke (delete) a key")
    p_revoke.add_argument("key", help="The sk-... key value")
    p_revoke.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    p_revoke.set_defaults(func=cmd_revoke)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(args.func(args))
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
