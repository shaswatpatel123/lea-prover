"""Lea CLI — minimal entry point."""

import argparse
import sys

from .agent import run, list_sessions, DEFAULT_MODEL


def main():
    parser = argparse.ArgumentParser(
        description="Lea — a minimal Lean 4 formalization agent",
    )
    parser.add_argument(
        "task",
        nargs="?",
        help="Math statement to formalize (or reads from stdin if omitted).",
    )
    parser.add_argument(
        "-m", "--model", default=DEFAULT_MODEL, help=f"Model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "-p", "--provider", default=None, help="Provider: gemini, anthropic, openai (auto-detected from model name if omitted)",
    )
    parser.add_argument(
        "--max-turns", type=int, default=None, help="Max agent turns (default: unlimited)",
    )
    parser.add_argument(
        "--resume", nargs="?", const=True, default=False,
        help="Resume a session. Pass a session ID, or omit to resume the most recent.",
    )
    parser.add_argument(
        "--sessions", action="store_true", help="List recent sessions and exit.",
    )

    args = parser.parse_args()

    if args.sessions:
        sessions = list_sessions()
        if not sessions:
            print("No sessions found.")
        for s in sessions:
            print(f"  {s['id']}  {s['model']:30s}  {s['turns']:>3} turns  {s['task']}")
        return

    task = args.task
    if not task and not args.resume:
        if sys.stdin.isatty():
            parser.print_help()
            sys.exit(1)
        task = sys.stdin.read().strip()

    result = run(
        task=task or "",
        model=args.model,
        max_turns=args.max_turns,
        provider=args.provider,
        resume=args.resume,
    )
    print(result)


if __name__ == "__main__":
    main()
