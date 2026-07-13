import argparse
import logging
import sys

from . import runner


def main() -> int:
    parser = argparse.ArgumentParser(prog="alerter", description="NBA close-game email alerter")
    parser.add_argument(
        "mode",
        choices=["run", "simulate", "check-schedule"],
        help="run: schedule-aware polling; simulate: replay a fixture and send a real test email; "
             "check-schedule: show today's scoreboard and what the runner would do",
    )
    parser.add_argument("--fixture", help="fixture JSON path for simulate (default: close_game_q4)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.mode == "run":
        return runner.run()
    if args.mode == "simulate":
        return runner.simulate(args.fixture)
    return runner.check_schedule()


if __name__ == "__main__":
    sys.exit(main())
