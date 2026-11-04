# How this checker works:
#
# 1. Reads the two excels separately (the data and the plan)
#
# 2. Rebuilds the plan from those sheets and tests it against the rules:
# - every shipment served once (LTL or truck)
# - at most one tour / tractor / day
# - stop order: DEPART home, pickups & deliveries, RETURN home empty
# - travel from distance sheet 
# - pickup time < delivery time
# - pickup @ origin -> delivery @ destination
# - handling and waiting (using plan times)
# - times must match -> arrival = previous departure + driver hours
# - service finishes inside shipment window + facility hours
# - depot hours + driver's shift
# - both events on shipment day
# - weight, floor length, and pallets after every stop
# - max driving, duty, and stops
# - LTL cost and tour cost rebuilt from rate sheets
#
# 3. print "OK" for each check that passes, raise ValueError if rule failed
#
# to add in main checker: LIFO, driver breaks, compatibility, reefer.


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
    """Checks the Excel sheets, raises errors if any expected sheets are missing."""
    missing = []
    for name in needed_sheets:
        if name not in sheets:
            missing.append(name)
    if missing:
        raise ValueError(f"{path} is missing sheets: {missing}")

# 3. load data and plan excels

def load_data(path):
    """Loads and reads the simplified workbook data."""
    sheets = pd.read_excel(path, sheet_name=None)
    check_sheets(sheets, needed_sheets_data, path.name)
    return sheets

def load_plan(path):
    """Loads and reads the proposed plan from the simplified model."""
    sheets = pd.read_excel(path, sheet_name=None)
    check_sheets(sheets, needed_sheets_plan, path.name)
    return sheets

# 4. check coverage

def ids_from_text(text):
    """Turns the tours sheet shipment ids into a list."""
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
        shipment_counts[shipment_id] = 0                                            # starts each shipment w/ count 0
    
    for shipment_id in plan["ltl"]["shipment_id"]:
        shipment_id = str(shipment_id)
        if shipment_id not in shipment_counts:
            raise ValueError(
                f"LTL list has unknown shipment {shipment_id}"                      # raises error if LTL id is not in data workbook
                )
        else:
            shipment_counts[shipment_id] = shipment_counts[shipment_id] + 1         # adds 1 to shipments with LTL (binary value)

    for tour in plan["tours"].itertuples(index=False):
        tractor_id = tour.tractor_id
        day = int(tour.day)
        tractor_and_day = (tractor_id, day)                                         # pairs trucks with days

        if tractor_and_day in used_tractors:
            raise ValueError(
                f"Tractor {tractor_id} is used twice on day {day}"                  # raises error if tractor is double-booked
                )

        used_tractors.add(tractor_and_day)

        tour_shipments = ids_from_text(tour.shipments)

        for shipment_id in tour_shipments:
            if shipment_id not in shipment_counts:
                raise ValueError(
                    f"Tour {tour.tour_id} has unknown shipment {shipment_id}"       # raises error if tour id is not in data workbook
                )
            else:
                shipment_counts[shipment_id] = shipment_counts[shipment_id] + 1
    
    for shipment_id in shipment_counts:
        count = shipment_counts[shipment_id]

        if count != 1:
            raise ValueError(
                f"Shipment {shipment_id} is served {count} time(s); expected 1"     # raises error if shipment served more than once
            )

    return "OK"

def check_route(data,plan):
    """Checks that depart & return = home, pickup < delivery, and right facilities are visited"""
    shipment_info = {}
    tractor_info = {}
    shipment_events = {}
    
    for row in data["shipments"].itertuples(index=False):
        shipment_info[str(row.shipment_id)] = row

    for row in data["tractors"].itertuples(index=False):
        tractor_info[row.tractor_id] = row
    
    for tour in plan["tours"].itertuples(index=False):
        stops = plan["stops"][plan["stops"]["tour_id"] == tour.tour_id]
        stops = stops.sort_values("seq")                                            # puts stops in order the truck is supposed to visit them
        stop_rows = list(stops.itertuples(index=False))  

        if len(stop_rows) == 0:
            raise ValueError(
                f"Tour {tour.tour_id} has no stops."                                # raises error if tour has 0 stops (invalid tour)
                )                                          

    for stop in stop_rows:
        event = stop.event

        if event in ("DEPART", "RETURN"):                                           # ignores depart/return events
            continue

        if event not in ("PICKUP", "DELIVERY"):
            raise ValueError(
                f"Tour {tour.tour_id} has unknown event {event}"                    # raises error for unknown event types
            )

        if pd.isna(stop.shipment_id):
            raise ValueError(
                f"Tour {tour.tour_id} {event} is missing a shipment id"             # raises error if no shipment id
            )

        shipment_id = str(stop.shipment_id)
        if shipment_id not in shipment_events:
            shipment_events[shipment_id] = []
        shipment_events[shipment_id].append((event, stop.location_id))


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