"""Check the simplified plan, or use --self-test to demonstrate three failures."""

import argparse
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import load_workbook

FOLDER = Path(__file__).resolve().parent
DATA_FILE = FOLDER / "data" / "project1_simplified.xlsx"
PLAN_FILE = FOLDER / "output" / "plan_simplified.xlsx"


def check_plan(data_file, plan_file, expected_error=None):
    # Reuse the workbook-driven checker. Neither checker imports a model.
    command = [
        sys.executable, str(FOLDER / "checker-full.py"),
        "--data", str(data_file), "--plan", str(plan_file),
    ]
    if expected_error is None:
        subprocess.run(command, check=True)
        return
    result = subprocess.run(command, capture_output=True, text=True)
    message = result.stderr.strip()
    if result.returncode != 1 or expected_error not in message:
        raise RuntimeError(f"Expected {expected_error!r}; checker returned {result.returncode}: {message}")
    print(f"Expected rejection: {message}")


def headers(sheet):
    return {cell.value: cell.column for cell in sheet[1]}


def test_rejections(data_file, plan_file):
    with TemporaryDirectory(prefix="simplified-check-") as folder:
        temporary = Path(folder)
        plan = load_workbook(plan_file)
        stops = plan["stops"]
        stop_col = headers(stops)
        pickup = next(row for row in range(2, stops.max_row + 1)
                      if stops.cell(row, stop_col["event"]).value == "PICKUP")
        shipment_id = stops.cell(pickup, stop_col["shipment_id"]).value

        # Raise a carried shipment's weight above the simplified trailer limit.
        data = load_workbook(data_file)
        shipments = data["shipments"]
        ship_col = headers(shipments)
        shipment = next(row for row in range(2, shipments.max_row + 1)
                        if shipments.cell(row, ship_col["shipment_id"]).value == shipment_id)
        shipments.cell(shipment, ship_col["weight_lb"]).value = 45_001
        overload_file = temporary / "overload.xlsx"
        data.save(overload_file)
        check_plan(overload_file, plan_file, "weight_lb capacity exceeded")

        # Move handling past the pickup deadline while keeping wait and handling durations consistent.
        for name in ("service_start_abs", "service_end_abs", "departure_abs", "wait_hours"):
            stops.cell(pickup, stop_col[name]).value += 24
        window_file = temporary / "missed-window.xlsx"
        plan.save(window_file)
        check_plan(data_file, window_file, "handling outside window/facility hours")

        # Assign a second tour on the first tour's day to the same tractor.
        plan = load_workbook(plan_file)
        tours = plan["tours"]
        tour_col = headers(tours)
        day = tours.cell(2, tour_col["day"]).value
        second_tour = next(row for row in range(3, tours.max_row + 1)
                           if tours.cell(row, tour_col["day"]).value == day)
        tours.cell(second_tour, tour_col["tractor_id"]).value = tours.cell(2, tour_col["tractor_id"]).value
        double_booked_file = temporary / "double-booked.xlsx"
        plan.save(double_booked_file)
        check_plan(data_file, double_booked_file, "Plan truck/day: blank or duplicate IDs")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA_FILE)
    parser.add_argument("--plan", type=Path, default=PLAN_FILE)
    parser.add_argument("--self-test", action="store_true", help="also check three intentionally invalid copies")
    args = parser.parse_args()
    check_plan(args.data, args.plan)
    if args.self_test:
        test_rejections(args.data, args.plan)


if __name__ == "__main__":
    main()
