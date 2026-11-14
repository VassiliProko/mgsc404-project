# How this checker works:
#
# 1. Reads the two excels separately (the data and the plan)
# - nothing is imported from the gurobi model (independent)
# - every limit comes from the workbook, nothing typed in (no 8 h, 12 h, 45000 lb)
# - checks the workbook itself first: unique ids, links between sheets, hours in order,
#   a travel row for every pair, 1 LTL rate per shipment
#
# 2. Rebuilds the plan from those sheets and tests it against the rules:
# - every shipment served once (LTL or truck)
# - at most one tour / tractor / day
# - stop order: DEPART home, pickups & deliveries, RETURN home empty
# - travel from distance sheet
# - pickup time < delivery time
# - pickup @ origin -> delivery @ destination
# - handling and waiting (using plan times) -> waiting = service start - arrival
# - times must match -> arrival = previous departure + driver hours
# - service finishes inside shipment window + facility hours
# - depot hours + driver's shift
# - both events on shipment day
# - weight, floor length, and pallets after every stop
# - max driving, duty, and stops
# - rest between days
# - LTL cost and tour cost rebuilt from rate sheets -> total has to match the plan's objective
#
# 3. print "OK" for each check that passes
# - each check appends to one error list. OK means that check added nothing
#
# at the end: ACCEPT or REJECT + every error message
#
# 4. self-test: python checker-simplified.py --self-test
# - real plan has to ACCEPT
# - 3 broken copies have to REJECT: double-booked tractor, missed window, overload
# - copies are made in memory, the excel is never touched
#
# !!! passing proves the plan is feasible, not optimal
#
# to add in main checker: LIFO, driver breaks, compatibility, reefer.


# 0. import packages

import sys
import time
import pandas as pd
from pathlib import Path

# 1. import files

FOLDER = Path(__file__).resolve().parent
DATA_FILE = FOLDER / "data" / "project1_simplified.xlsx"
PLAN_FILE = FOLDER / "output" / "plan_simplified.xlsx"
EPS = 1e-6                                                                          # tiny tolerance when comparing hours

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
    "ltl",
    "summary"                                                                       # has objective_cad (the cost the model claims)
]

def check_sheets(sheets, needed_sheets, path):
    """Checks the Excel sheets, raises errors if any expected sheets are missing."""
    missing = []
    for name in needed_sheets:
        if name not in sheets:
            missing.append(name)
    if missing:
        raise ValueError(f"{path} is missing sheets: {missing}")                     # still raises: broken file, not a bad plan

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
    if "shipment_id" not in sheets["ltl"].columns:                                  # 0 LTL shipments -> excel sheet has no headers
        sheets["ltl"] = pd.DataFrame(columns=["shipment_id", "day", "ltl_cost_cad"])
    return sheets

# 4. lookups and helpers

def build_lookups(data):
    """Reads every workbook sheet once and turns it into a dictionary (id -> row)."""
    lookups = {}
    lookups["sheets"] = data                                                        # raw sheets, only check_data uses them

    lookups["shipments"] = {}
    for row in data["shipments"].itertuples(index=False):
        lookups["shipments"][str(row.shipment_id)] = row                            # S001 -> origin, windows, weight...

    lookups["tractors"] = {}
    for row in data["tractors"].itertuples(index=False):
        lookups["tractors"][row.tractor_id] = row                                   # T01 -> home depot, equipment, shift

    lookups["facilities"] = {}
    for row in data["facilities"].itertuples(index=False):
        lookups["facilities"][row.facility_id] = row                                # F01 -> opening hours

    lookups["depots"] = {}
    for row in data["depots"].itertuples(index=False):
        lookups["depots"][row.depot_id] = row                                       # D01 -> opening hours

    lookups["equipment"] = {}
    for row in data["equipment"].itertuples(index=False):
        lookups["equipment"][row.equipment_id] = row                                # DRY -> weight, feet, pallet capacity

    lookups["tl_costs"] = {}
    for row in data["tl_costs"].itertuples(index=False):
        lookups["tl_costs"][row.equipment_id] = row                                 # DRY -> $/mile, minimum, extra stop fee

    lookups["ltl_rates"] = data["ltl_rates"]
    lookups["rules"] = data["driver_rules"].set_index("parameter")["value"].to_dict()     # max drive, max duty, min rest
    lookups["params"] = data["parameters"].set_index("parameter")["value"].to_dict()      # max stops, horizon

    lookups["miles"] = {}
    lookups["drive"] = {}
    for row in data["distance_matrix"].itertuples(index=False):
        lookups["miles"][row.from_id, row.to_id] = float(row.distance_miles)       # directed: A -> B is not B -> A
        lookups["drive"][row.from_id, row.to_id] = float(row.drive_hours)
    return lookups

def close(a, b, tol=EPS):
    """Checks if two numbers match (tiny float gap allowed). A blank cell never matches."""
    if pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) <= tol

def lane(lookups, a, b):
    """Gives miles and drive hours from a to b. None if there is no travel row."""
    if a == b:
        return 0.0, 0.0                                                             # same place = 0 (no row in the sheet)
    if (a, b) not in lookups["miles"]:
        return None
    return lookups["miles"][a, b], lookups["drive"][a, b]

def stops_of(plan, tour_id):
    """Gives one tour's stop rows."""
    stops = plan["stops"][plan["stops"]["tour_id"] == tour_id]
    stops = stops.sort_values("seq")                                                # puts stops in order the truck is supposed to visit them
    return list(stops.itertuples(index=False))

# 5. check data

def check_data(lookups, plan, errors, rebuilt):
    """Checks the workbook itself before judging the plan: unique ids, links between sheets, hours, travel rows, LTL rates."""
    sheets = lookups["sheets"]

    for name, key in [("shipments", "shipment_id"), ("facilities", "facility_id"), ("depots", "depot_id"),
                      ("tractors", "tractor_id"), ("equipment", "equipment_id")]:
        if sheets[name][key].duplicated().any():
            errors.append(f"Data: duplicate ids in {name}")                         # same id twice -> lookups would only keep one

    for facility_id, f in lookups["facilities"].items():
        if not f.open_hour < f.close_hour:
            errors.append(f"Data: facility {facility_id} hours not in order")
    for depot_id, d in lookups["depots"].items():
        if not d.open_hour < d.close_hour:
            errors.append(f"Data: depot {depot_id} hours not in order")

    for tractor_id, t in lookups["tractors"].items():
        if (t.home_depot_id not in lookups["depots"] or t.equipment_id not in lookups["equipment"]
                or t.equipment_id not in lookups["tl_costs"]):
            errors.append(f"Data: tractor {tractor_id} has an unknown depot or equipment")   # links between sheets
        if not t.shift_start_hour < t.shift_end_hour:
            errors.append(f"Data: tractor {tractor_id} shift not in order")

    locations = list(lookups["facilities"]) + list(lookups["depots"])
    for a in locations:
        for b in locations:
            if a != b and (a, b) not in lookups["miles"]:
                errors.append(f"Data: missing travel row {a} -> {b}")              # every directed pair needs its own row
    if sheets["distance_matrix"].duplicated(["from_id", "to_id"]).any():
        errors.append("Data: a travel pair has more than one row")

    for shipment_id, s in lookups["shipments"].items():
        if s.origin_id not in lookups["facilities"] or s.destination_id not in lookups["facilities"]:
            errors.append(f"Data: {shipment_id} has an unknown facility")
        if not (s.pickup_open_hour < s.pickup_close_hour and s.delivery_open_hour < s.delivery_close_hour):
            errors.append(f"Data: {shipment_id} windows not in order")
        if min(s.weight_lb, s.linear_feet, s.pallets, s.pickup_service_hours, s.delivery_service_hours) <= 0:
            errors.append(f"Data: {shipment_id} has a demand or handling time <= 0")
        price = ltl_cost_of(lookups, s)                                             # priced once here, check_cost reuses it
        if price is None:
            errors.append(f"Data: {shipment_id} does not match exactly one LTL rate")   # never counts as $0
        rebuilt["ltl_prices"][shipment_id] = price

# 6. check coverage

def ids_from_text(text):
    """Turns the tours sheet shipment ids into a list."""
    ids = []
    for part in str(text).split(","):
        name = part.strip()
        if name:
            ids.append(name)
    return ids

def check_coverage(lookups, plan, errors, rebuilt):
    """Checks that 1 shipment equals 1 job, while also checking for tractor duplicates."""
    shipment_counts = {}
    used_tractors = set()

    for shipment_id in lookups["shipments"]:
        shipment_counts[shipment_id] = 0                                            # starts each shipment w/ count 0

    for shipment_id in plan["ltl"]["shipment_id"]:
        shipment_id = str(shipment_id)
        if shipment_id not in shipment_counts:
            errors.append(
                f"LTL list has unknown shipment {shipment_id}"                      # adds error if LTL id is not in data workbook
                )
        else:
            shipment_counts[shipment_id] = shipment_counts[shipment_id] + 1         # adds 1 to shipments with LTL (binary value)

    for tour in plan["tours"].itertuples(index=False):
        tractor_id = tour.tractor_id
        day = int(tour.day)
        tractor_and_day = (tractor_id, day)                                         # pairs trucks with days

        if tractor_and_day in used_tractors:
            errors.append(
                f"Tractor {tractor_id} is used twice on day {day}"                  # adds error if tractor is double-booked
                )

        used_tractors.add(tractor_and_day)

        tour_shipments = ids_from_text(tour.shipments)

        for shipment_id in tour_shipments:
            if shipment_id not in shipment_counts:
                errors.append(
                    f"Tour {tour.tour_id} has unknown shipment {shipment_id}"       # adds error if tour id is not in data workbook
                )
            else:
                shipment_counts[shipment_id] = shipment_counts[shipment_id] + 1

    for shipment_id in shipment_counts:
        count = shipment_counts[shipment_id]

        if count != 1:
            errors.append(
                f"Shipment {shipment_id} is served {count} time(s); expected 1"     # adds error if shipment served more than once (or 0 times)
            )

# 7. check route

def check_route(lookups, plan, errors, rebuilt):
    """Checks that depart & return = home, pickup < delivery, and right facilities are visited"""
    if plan["tours"]["tour_id"].duplicated().any():
        errors.append("Tours sheet has the same tour_id twice")
    known_tours = set(plan["tours"]["tour_id"])
    for tour_id in plan["stops"]["tour_id"].unique():
        if tour_id not in known_tours:
            errors.append(f"Stops sheet has rows for tour {tour_id}, which is not on the tours sheet")   # stops nobody would check
    if plan["stops"].duplicated(["tour_id", "seq"]).any():
        errors.append("Stops sheet has the same seq twice in one tour")            # stop order would be ambiguous

    for tour in plan["tours"].itertuples(index=False):
        stop_rows = stops_of(plan, tour.tour_id)

        if len(stop_rows) == 0:
            errors.append(
                f"Tour {tour.tour_id} has no stops."                                # adds error if tour has 0 stops (invalid tour)
                )
            continue

        if tour.tractor_id not in lookups["tractors"]:
            errors.append(
                f"Tour {tour.tour_id} uses unknown tractor {tour.tractor_id}"       # adds error if tractor is not in data workbook
                )
            continue

        home = lookups["tractors"][tour.tractor_id].home_depot_id                   # home depot from the WORKBOOK, not the plan
        first = stop_rows[0]
        last = stop_rows[-1]

        if first.event != "DEPART":
            errors.append(f"Tour {tour.tour_id} does not start with DEPART")
        if last.event != "RETURN":
            errors.append(f"Tour {tour.tour_id} does not end with RETURN")
        if first.location_id != home or last.location_id != home:
            errors.append(f"Tour {tour.tour_id} does not start and end at home depot {home}")

        shipment_events = {}                                                        # reset every tour, or tours bleed into each other

        for stop in stop_rows[1:-1]:                                                # ignores depart/return events (first and last row)
            event = stop.event

            if event not in ("PICKUP", "DELIVERY"):
                errors.append(
                    f"Tour {tour.tour_id} has unknown event {event}"                # adds error for unknown event types
                )
                continue

            if pd.isna(stop.shipment_id):
                errors.append(
                    f"Tour {tour.tour_id} {event} is missing a shipment id"         # adds error if no shipment id
                )
                continue

            shipment_id = str(stop.shipment_id)
            if shipment_id not in shipment_events:
                shipment_events[shipment_id] = []
            shipment_events[shipment_id].append((event, stop.location_id))          # e.g. S001 -> [PICKUP @ F01, DELIVERY @ F03]

        claimed = ids_from_text(tour.shipments)                                     # shipments the tours sheet says are on this truck

        for shipment_id in claimed:
            ship = lookups["shipments"].get(shipment_id)
            if ship is None:
                continue                                                            # unknown id, coverage already caught it
            if shipment_id not in shipment_events:
                errors.append(f"Tour {tour.tour_id} claims {shipment_id} but it has no stops")
                continue
            expected = [("PICKUP", ship.origin_id), ("DELIVERY", ship.destination_id)]
            if shipment_events[shipment_id] != expected:                            # pickup @ origin first, then delivery @ destination
                errors.append(f"Tour {tour.tour_id} has a bad stop order for {shipment_id}: {shipment_events[shipment_id]}")

        for shipment_id in shipment_events:
            if shipment_id not in claimed:
                errors.append(f"Tour {tour.tour_id} stop list has extra shipment {shipment_id}")   # freight not listed on the tours sheet

# 8. check schedule

def check_schedule(lookups, plan, errors, rebuilt):
    """Checks each tour by replaying it from DEPART: times, windows, load, driver limits, and tour cost."""
    max_drive = float(lookups["rules"]["max_drive_hours"])                          # 8 h in simplified
    max_duty = float(lookups["rules"]["max_duty_hours"])                            # 12 h in simplified
    max_stops = int(lookups["params"]["max_stops"])                                 # 6 events = max 3 shipments
    horizon_end = int(lookups["params"]["horizon_end_day"])                         # ends at 00:00 on day 4

    for tour in plan["tours"].itertuples(index=False):
        tractor = lookups["tractors"].get(tour.tractor_id)
        stop_rows = stops_of(plan, tour.tour_id)
        if tractor is None or len(stop_rows) < 2 or stop_rows[0].event != "DEPART":
            continue                                                                # bad tour shape, check_route already caught it

        home = tractor.home_depot_id
        depot = lookups["depots"][home]
        cap = lookups["equipment"][tractor.equipment_id]                            # this tractor's trailer, not the biggest one
        rate = lookups["tl_costs"][tractor.equipment_id]
        day = int(tour.day)

        earliest = 24 * day + max(float(tractor.shift_start_hour), float(depot.open_hour))     # can't leave before shift or depot opens
        latest = 24 * day + min(float(tractor.shift_end_hour), float(depot.close_hour))         # back before shift ends or depot closes
        latest = min(latest, 24 * horizon_end)                                                  # and before the horizon ends

        if pd.isna(stop_rows[0].departure_abs):
            errors.append(f"Tour {tour.tour_id} has no departure time")
            continue
        depart = float(stop_rows[0].departure_abs)                                  # only time taken from the plan to start the clock
        if depart + EPS < earliest:
            errors.append(f"Tour {tour.tour_id} leaves before the shift or depot opens")

        now = depart                                                                # our own clock
        here = home                                                                 # where the truck is right now
        total_drive = 0.0
        total_miles = 0.0
        load_w = 0.0                                                                # weight on board
        load_f = 0.0                                                                # floor feet on board
        load_p = 0.0                                                                # pallets on board
        n_events = 0                                                                # pickups + deliveries

        for stop in stop_rows[1:]:
            leg = lane(lookups, here, stop.location_id)
            if leg is None:
                errors.append(f"Tour {tour.tour_id} has no travel row {here} -> {stop.location_id}")
                break
            leg_miles, leg_drive = leg
            arrival = now + leg_drive                                               # arrival = previous departure + driver hours
            total_drive = total_drive + leg_drive
            total_miles = total_miles + leg_miles

            if not close(stop.arrival_abs, arrival):                                # plan's arrival has to match our clock
                errors.append(f"Tour {tour.tour_id} seq {stop.seq} reports arrival {stop.arrival_abs}, replay gives {arrival}")

            # end of tour: home, empty, on time, inside driver limits
            if stop.event == "RETURN":
                if stop.location_id != home:
                    errors.append(f"Tour {tour.tour_id} returns to the wrong depot")
                if abs(load_w) + abs(load_f) + abs(load_p) > EPS:
                    errors.append(f"Tour {tour.tour_id} returns with freight still on board")
                if arrival > latest + EPS:
                    errors.append(f"Tour {tour.tour_id} returns at {arrival}, after the shift, depot close, or horizon ({latest})")
                if total_drive > max_drive + EPS:
                    errors.append(f"Tour {tour.tour_id} drives {total_drive} h, limit is {max_drive}")
                duty = arrival - depart                                             # duty = leave depot -> back at depot (driving + handling + waiting)
                if duty > max_duty + EPS:
                    errors.append(f"Tour {tour.tour_id} duty is {duty} h, limit is {max_duty}")
                if n_events > max_stops:
                    errors.append(f"Tour {tour.tour_id} has {n_events} service events, limit is {max_stops}")

                # tour cost = max(minimum, miles x rate) + fee for every event after the first 2
                linehaul = max(float(rate.minimum_linehaul_cad), float(rate.linehaul_cad_per_mile) * total_miles)   # empty depot miles count too
                stop_fee = max(0, n_events - 2) * float(rate.extra_stop_cad)
                cost = linehaul + stop_fee
                if not close(tour.tour_cost_cad, cost, 0.01):
                    errors.append(f"Tour {tour.tour_id} cost is {tour.tour_cost_cad}, rebuilt {cost:.2f}")
                rebuilt["tour_total"] = rebuilt["tour_total"] + cost                # check_cost adds the LTL part later
                rebuilt["spans"].setdefault(tour.tractor_id, []).append((depart, arrival))   # check_rest uses this
                break                                                               # RETURN is the last stop

            ship = lookups["shipments"].get(str(stop.shipment_id))
            if ship is None or stop.event not in ("PICKUP", "DELIVERY"):
                break                                                               # bad stop, check_route already caught it
            if int(ship.day) != day:
                errors.append(f"Tour {tour.tour_id} serves {stop.shipment_id} on day {day}, shipment day is {int(ship.day)}")

            if stop.event == "PICKUP":
                open_hour = float(ship.pickup_open_hour)
                close_hour = float(ship.pickup_close_hour)
                service = float(ship.pickup_service_hours)
                sign = 1                                                            # freight goes on
            else:
                open_hour = float(ship.delivery_open_hour)
                close_hour = float(ship.delivery_close_hour)
                service = float(ship.delivery_service_hours)
                sign = -1                                                           # freight comes off

            facility = lookups["facilities"].get(stop.location_id)
            if facility is None or pd.isna(stop.service_start_abs):
                errors.append(f"Tour {tour.tour_id} seq {stop.seq} cannot be timed (facility or service start missing)")
                break

            earliest_start = 24 * day + max(open_hour, float(facility.open_hour))   # later of window open & facility open
            latest_finish = 24 * day + min(close_hour, float(facility.close_hour))  # earlier of window close & facility close

            start = float(stop.service_start_abs)                                   # plan says when handling started
            finish = start + service                                                # handling time comes from the workbook
            wait = start - arrival                                                  # waiting = service start - arrival

            if wait < -EPS:
                errors.append(f"Tour {tour.tour_id} {stop.shipment_id} starts service before the truck arrives")
            if start + EPS < earliest_start or finish > latest_finish + EPS:        # close = latest FINISH, not latest arrival
                errors.append(f"Tour {tour.tour_id} {stop.shipment_id} misses the window or facility hours "
                              f"(service {start}-{finish}, allowed {earliest_start}-{latest_finish})")

            load_w = load_w + sign * float(ship.weight_lb)                          # load after this stop
            load_f = load_f + sign * float(ship.linear_feet)
            load_p = load_p + sign * float(ship.pallets)
            n_events = n_events + 1
            if (load_w > float(cap.weight_capacity_lb) + EPS                        # weight, floor length, and pallets after every stop
                    or load_f > float(cap.length_capacity_feet) + EPS
                    or load_p > float(cap.pallet_capacity) + EPS):
                errors.append(f"Tour {tour.tour_id} over capacity after {stop.event} {stop.shipment_id}: "
                              f"{load_w:.0f} lb, {load_f:.0f} ft, {load_p:.0f} pallets")

            now = finish                                                            # truck leaves once handling is done
            here = stop.location_id

# 9. check rest

def check_rest(lookups, plan, errors, rebuilt):
    """Checks the time off between one day's return and the next day's departure, per tractor."""
    min_rest = float(lookups["rules"]["min_rest_hours"])                            # 12 h in simplified
    for tractor_id in rebuilt["spans"]:
        spans = sorted(rebuilt["spans"][tractor_id])                                # (depart, return) in time order
        for previous, following in zip(spans, spans[1:]):
            gap = following[0] - previous[1]                                        # next departure - last return
            if gap + EPS < min_rest:
                errors.append(f"Tractor {tractor_id} rests {gap:.2f} h between tours, minimum is {min_rest}")

# 10. check cost

def ltl_cost_of(lookups, ship):
    """Finds one shipment's LTL price -> base x (1 + fuel)."""
    direct = lane(lookups, ship.origin_id, ship.destination_id)                     # DIRECT origin -> destination miles, not tour miles
    if direct is None:
        return None
    direct_miles = direct[0]
    rates = lookups["ltl_rates"]
    match = rates[
        (rates["freight_class"] == ship.freight_class)
        & (rates["weight_from_lb"] <= ship.weight_lb)                               # lower bound included
        & (ship.weight_lb < rates["weight_to_lb"])                                  # upper bound excluded
        & (rates["distance_from_miles"] <= direct_miles)
        & (direct_miles < rates["distance_to_miles"])
    ]
    if len(match) != 1:
        return None                                                                 # 0 or 2+ matches = input error, never $0
    row = match.iloc[0]
    return float(row["base_cost_cad"]) * (1 + float(row["fuel_surcharge_pct"]))

def check_cost(lookups, plan, errors, rebuilt):
    """Checks every LTL price, then compares the whole bill to the plan's objective_cad."""
    ltl_total = 0.0
    for row in plan["ltl"].itertuples(index=False):
        cost = rebuilt["ltl_prices"].get(str(row.shipment_id))                      # priced once in check_data
        if cost is None:
            continue                                                                # unknown id or no LTL rate, already caught
        if not close(row.ltl_cost_cad, cost, 0.01):
            errors.append(f"{row.shipment_id} LTL cost is {row.ltl_cost_cad}, rebuilt {cost:.2f}")
        ltl_total = ltl_total + cost

    all_ltl = 0.0                                                                   # baseline: every shipment on LTL
    for cost in rebuilt["ltl_prices"].values():
        if cost is not None:                                                        # no LTL rate -> check_data already reported it
            all_ltl = all_ltl + cost

    total = rebuilt["tour_total"] + ltl_total                                       # tour costs (from check_schedule) + LTL costs
    summary = plan["summary"]
    claimed = summary.loc[summary["item"] == "objective_cad", "value"]              # what the model says the plan costs
    if len(claimed) != 1:
        errors.append("summary sheet has no objective_cad")
    elif not close(claimed.iloc[0], total, 0.01):
        errors.append(f"Claimed objective {float(claimed.iloc[0]):.2f} disagrees with rebuilt cost {total:.2f}")

    rebuilt["total"] = total
    rebuilt["all_ltl"] = all_ltl

# 11. run every check

CHECKS = [                                                                          # order matters:
    ("Data", check_data),                                                           # fills ltl_prices
    ("Coverage", check_coverage),
    ("Stop order", check_route),
    ("Schedule", check_schedule),                                                   # fills tour_total and spans
    ("Rest", check_rest),                                                           # needs spans
    ("Cost", check_cost),                                                           # needs tour_total + ltl_prices
]

def check_plan(data, plan, talk=True):
    """Runs every check on one plan and gives back the list of errors (empty list = feasible)."""
    lookups = build_lookups(data)
    errors = []
    rebuilt = {"tour_total": 0.0, "spans": {}, "ltl_prices": {}}                    # numbers we rebuild ourselves

    for name, check in CHECKS:
        before = len(errors)
        check(lookups, plan, errors, rebuilt)
        added = len(errors) - before                                                # how many errors this check added
        if talk:
            if added == 0:
                print(f"{name} status: OK")
            else:
                print(f"{name} status: {added} problem(s)")

    if talk:
        savings = rebuilt["all_ltl"] - rebuilt["total"]
        print(f"Rebuilt total: {rebuilt['total']:,.2f} CAD")
        print(f"All-LTL baseline: {rebuilt['all_ltl']:,.2f} CAD")
        print(f"Savings: {savings:,.2f} CAD ({100 * savings / rebuilt['all_ltl']:.2f}%)")
    return errors

# 12. self-test

def copy_plan(plan):
    """Copies every plan sheet, so the broken versions never touch the real plan."""
    copied = {}
    for name in plan:
        copied[name] = plan[name].copy()
    return copied

def flagged(errors, text):
    """Checks if any error message has this text in it."""
    for message in errors:
        if text in message:
            return True
    return False

def break_double_book(plan, lookups):
    """Breaks the plan: gives a tour the tractor of another tour on the same day."""
    broken = copy_plan(plan)
    tours = broken["tours"]
    first_seen = {}                                                                 # (day, depot) -> first tractor seen
    for tour in tours.itertuples(index=False):
        key = (int(tour.day), lookups["tractors"][tour.tractor_id].home_depot_id)  # same depot -> only the booking is wrong
        if key in first_seen:
            tours.loc[tours["tour_id"] == tour.tour_id, "tractor_id"] = first_seen[key]
            return broken
        first_seen[key] = tour.tractor_id
    return None                                                                     # no 2 tours share a day & depot

def break_window(plan, lookups):
    """Breaks the plan: slides one whole tour later so its first pickup ends 15 min too late."""
    broken = copy_plan(plan)
    stops = broken["stops"]
    tour_id = broken["tours"].iloc[0]["tour_id"]
    mask = stops["tour_id"] == tour_id
    first_pickup = stops[mask].sort_values("seq").iloc[1]                           # seq 0 is DEPART
    ship = lookups["shipments"][str(first_pickup["shipment_id"])]
    facility = lookups["facilities"][first_pickup["location_id"]]
    must_end = 24 * int(first_pickup["day"]) + min(float(ship.pickup_close_hour), float(facility.close_hour))
    slide = must_end - float(first_pickup["service_end_abs"]) + 0.25               # 15 min past the close
    for col in ["arrival_abs", "service_start_abs", "service_end_abs", "departure_abs"]:
        stops.loc[mask, col] = stops.loc[mask, col] + slide                         # every clock moves the same -> drive math still matches
    return broken

def break_overload(plan, lookups):
    """Breaks the plan: moves an LTL shipment onto a truck that can't fit it."""
    broken = copy_plan(plan)                                                        # editing load_weight_lb wouldn't work: load is rebuilt from the workbook
    tours = broken["tours"]
    stops = broken["stops"]
    ltl = broken["ltl"]
    for lrow in ltl.itertuples(index=False):
        new_id = str(lrow.shipment_id)
        new = lookups["shipments"][new_id]
        for tour in tours.itertuples(index=False):
            if int(tour.day) != int(new.day):                                       # same day only
                continue
            cap = lookups["equipment"][lookups["tractors"][tour.tractor_id].equipment_id]

            w = f = p = peak_w = peak_f = peak_p = 0.0                              # heaviest moment of this tour right now
            for stop in stops_of(broken, tour.tour_id):
                if stop.event in ("PICKUP", "DELIVERY"):
                    other = lookups["shipments"][str(stop.shipment_id)]
                    sign = 1 if stop.event == "PICKUP" else -1
                    w, f, p = w + sign * other.weight_lb, f + sign * other.linear_feet, p + sign * other.pallets
                    peak_w, peak_f, peak_p = max(peak_w, w), max(peak_f, f), max(peak_p, p)
            if (new.weight_lb + peak_w <= cap.weight_capacity_lb
                    and new.linear_feet + peak_f <= cap.length_capacity_feet
                    and new.pallets + peak_p <= cap.pallet_capacity):
                continue                                                            # still fits -> try the next tour

            rows = stops[stops["tour_id"] == tour.tour_id].sort_values("seq")
            pickup = rows.iloc[1].copy()                                            # copy a real stop row so every column exists
            pickup["event"] = "PICKUP"
            pickup["shipment_id"] = new_id
            pickup["location_id"] = new.origin_id
            delivery = pickup.copy()
            delivery["event"] = "DELIVERY"
            delivery["location_id"] = new.destination_id
            new_rows = pd.concat([rows.iloc[:1], pd.DataFrame([pickup]), rows.iloc[1:-1],    # pickup right after DEPART,
                                  pd.DataFrame([delivery]), rows.iloc[-1:]], ignore_index=True)   # delivery right before RETURN
            new_rows["seq"] = range(len(new_rows))                                  # renumbers the stop order
            broken["stops"] = pd.concat([stops[stops["tour_id"] != tour.tour_id], new_rows], ignore_index=True)
            tours.loc[tours["tour_id"] == tour.tour_id, "shipments"] = str(tour.shipments) + "," + new_id
            broken["ltl"] = ltl[ltl["shipment_id"].astype(str) != new_id]           # no longer on LTL
            return broken
    return None                                                                     # no LTL shipment is big enough

def self_test(data, plan):
    """Checks that the real plan passes and each broken copy fails for the right reason."""
    lookups = build_lookups(data)
    passed = 0

    errors = check_plan(data, plan, talk=False)
    if errors:
        print("SELF-TEST FAIL: the real plan was rejected")
        for message in errors:
            print("  ", message)
    else:
        print("real plan: ACCEPT")
        passed = passed + 1

    cases = [                                                                       # (name, broken plan, text that has to show up)
        ("double-booked tractor", break_double_book(plan, lookups), "used twice"),
        ("missed window", break_window(plan, lookups), "misses the window"),
        ("overload", break_overload(plan, lookups), "over capacity"),
    ]
    for name, broken, text in cases:
        if broken is None:                                                          # check this BEFORE running the checker on it
            print(f"SELF-TEST FAIL: could not build the {name} case from this plan")
            continue
        errors = check_plan(data, broken, talk=False)
        if flagged(errors, text):
            print(f"{name}: REJECT, flagged '{text}' ({len(errors)} problem(s) in total)")
            passed = passed + 1
        else:
            print(f"SELF-TEST FAIL: {name} was not flagged")

    print(f"self-test: {passed} of 4 passes")
    return passed == 4

# 13. main

def main():
    started = time.perf_counter()
    data = load_data(DATA_FILE)
    plan = load_plan(PLAN_FILE)

    if "--self-test" in sys.argv:                                                   # python checker-simplified.py --self-test
        ok = self_test(data, plan)
        print(f"Checker time: {time.perf_counter() - started:.2f} s")
        sys.exit(0 if ok else 1)

    print(f"""Workbook:
    number of shipments: {len(data["shipments"])}
    number of tractors: {len(data["tractors"])}""")
    print(f"""Plan:
    number of tours: {len(plan["tours"])}
    number of stops: {len(plan["stops"])}
    number of ltl: {len(plan["ltl"])}""")

    errors = check_plan(data, plan, talk=True)
    print(f"Checker time: {time.perf_counter() - started:.2f} s")

    if errors:
        print("REJECT")
        for message in errors:
            print("  ", message)
        sys.exit(1)                                                                 # v4 runs this w/ check=True -> stops loudly on REJECT
    print("ACCEPT")
    print("Passing this checker proves the plan is feasible, not optimal.")


if __name__ == "__main__":
    main()