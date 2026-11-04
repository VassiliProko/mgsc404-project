# checker for the simplified workbook plan

# 0. import packages

import pandas as pd
from pathlib import Path

# 1. import files

FOLDER = Path(__file__).resolve().parent
DATA_FILE = FOLDER / "data" / "project1_simplified.xlsx"
PLAN_FILE = FOLDER / "output" / "plan_simplified.xlsx"

# 2. check sheets

needed_sheets_data = [
    "shipments",
    "facilities",
    "distance_matrix",
    "ltl_rates",
    "tl_costs",
    "equipment",
    "tractors",
    "depots",
    "driver_rules",
    "parameters"
]

needed_sheets_plan = [
    "tours",
    "stops",
    "ltl"
]

def check_sheets(sheets, needed_sheets, path):
    """Checks the Excel sheets, raises errors if any expected sheets are missing.
    Ensures proper imports of the data and plan Excels."""
    missing = []
    for name in needed_sheets:
        if name not in sheets:
            missing.append(name)
    if missing:
        raise ValueError(f"{path} is missing sheets: {missing}")

# 3. load data and plan excels

def load_data(path):
    """Loads and reads the original (simplified) workbook data."""
    sheets = pd.read_excel(path, sheet_name=None)
    check_sheets(sheets, needed_sheets_data, path.name)
    return sheets

def load_plan(path):
    """Loads and reads the optimized plan outputted by the simplified model."""
    sheets = pd.read_excel(path, sheet_name=None)
    check_sheets(sheets, needed_sheets_plan, path.name)
    return sheets

# 4. check coverage

def ids_from_text(text):
    ids = []
    for part in str(text).split(","):
        name = part.strip()
        if name:
            ids.append(name)
    return ids

def check_coverage(data, plan):
    """Checks that 1 shipment equals 1 job, while also checking for tractor duplicates."""
    shipment_counts = {}
    used_tractors = set()

    for shipment_id in data["shipments"]["shipment_id"]:
        shipment_id = str(shipment_id)
        shipment_counts[shipment_id] = 0                                    # starts each shipment w/ count 0
    
    for shipment_id in plan["ltl"]["shipment_id"]:
        shipment_id = str(shipment_id)
        if shipment_id not in shipment_counts:
            raise ValueError(
                f"LTL list has unknown shipment {shipment_id}"              # raises error if shipment_id from plan is not in data workbook
                )
        else:
            shipment_counts[shipment_id] = shipment_counts[shipment_id] + 1 # adds 1 to shipments with LTL (binary value)

    for tour in plan["tours"].itertuples(index=False):
        tractor_id = tour.tractor_id
        day = int(tour.day)
        tractor_and_day = (tractor_id, day)

        if tractor_and_day in used_tractors:
            raise ValueError(f"Tractor {tractor_id} is used twice on day {day}")

        used_tractors.add(tractor_and_day)

        tour_shipments = ids_from_text(tour.shipments)

        for shipment_id in tour_shipments:
            if shipment_id not in shipment_counts:
                raise ValueError(
                    f"Tour {tour.tour_id} has unknown shipment {shipment_id}"
                )
            else:
                shipment_counts[shipment_id] = shipment_counts[shipment_id] + 1
    
    for shipment_id in shipment_counts:
        count = shipment_counts[shipment_id]

        if count != 1:
            raise ValueError(
                f"Shipment {shipment_id} is servec {count} time(s); expected 1"
            )

    return "OK"

def main():
    data = load_data(DATA_FILE)
    plan = load_plan(PLAN_FILE)

    print(f"""Workbook:
    number of shipments: {len(data["shipments"])}
    number of tractors: {len(data["tractors"])}""")
    print(f"""Plan:
    number of tours: {len(plan["tours"])}
    number of stops: {len(plan["stops"])}
    number of ltl: {len(plan["ltl"])}""")

    coverage = check_coverage(data, plan)
    print(f"Coverage status: {coverage}")
    

if __name__ == "__main__":
    main()


