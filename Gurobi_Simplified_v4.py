# How this program works:
#   1. First we make a list of every truck tour that could work (1, 2 or 3
#      shipments, every possible stop order, every truck). A tour is only
#      kept if it follows all the rules (routes, time windows, driving and
#      duty hours, capacity, stop limit) and we work out what it costs.
#   2. Then Gurobi picks the cheapest combination:
#        variables:   select_tour[r] and use_ltl[i] (both binary)
#        objective:   minimize total tour cost + total LTL cost
#        constraints: every shipment is served exactly once (truck or LTL)
#                     and each truck does at most one tour per day
#   3. The results get saved in an Excel file

import itertools
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import gurobipy as gp
from gurobipy import GRB

# settings we can change
FOLDER = Path(__file__).resolve().parent
DATA_FILE = FOLDER / "data" / "project1_simplified.xlsx"
OUTPUT_FOLDER = FOLDER / "output"
OUTPUT_FOLDER.mkdir(exist_ok=True)          # creates the folder if it doesn't exist yet
PLAN_FILE = OUTPUT_FOLDER / "plan_simplified.xlsx"

TIME_LIMIT_SECONDS = 600          # Gurobi time limit 
EPS = 1e-6                        # small tolerance when comparing numbers


# Step 1: read the Excel file
def read_workbook(path):
    sheets = pd.read_excel(path, sheet_name=None)
    needed = ["shipments", "facilities", "distance_matrix", "ltl_rates",
              "tl_costs", "equipment", "tractors", "depots",
              "driver_rules", "parameters"]
    for name in needed:
        if name not in sheets:
            raise ValueError(f"Sheet '{name}' is missing from {path.name}")
    return sheets


sheets = read_workbook(DATA_FILE)

shipments_df = sheets["shipments"]
facilities_df = sheets["facilities"]
distance_df = sheets["distance_matrix"]
ltl_df = sheets["ltl_rates"]
tl_costs_df = sheets["tl_costs"]
equipment_df = sheets["equipment"]
tractors_df = sheets["tractors"]
depots_df = sheets["depots"]

# turn each sheet into a dictionary (by ID)
SHIP = shipments_df.set_index("shipment_id").to_dict("index")
FAC = facilities_df.set_index("facility_id").to_dict("index")
DEPOT = depots_df.set_index("depot_id").to_dict("index")
EQUIP = equipment_df.set_index("equipment_id").to_dict("index")
TLCOST = tl_costs_df.set_index("equipment_id").to_dict("index")
TRACTOR = tractors_df.set_index("tractor_id").to_dict("index")

rules = sheets["driver_rules"].set_index("parameter")["value"].to_dict()
params = sheets["parameters"].set_index("parameter")["value"].to_dict()

# rule values from the file (simplified values in the comments)
max_drive_hours = float(rules["max_drive_hours"])          # 8 h
max_duty_hours = float(rules["max_duty_hours"])            # 12 h
min_rest_hours = float(rules["min_rest_hours"])            # 12 h
max_stops = int(params["max_stops"])                       # 6 stops = max 3 shipments
lifo_required = int(params["lifo_required"])               # 0 = side access
demand_days = int(params["demand_days"])                   # days 1-3
horizon_end_day = int(params["horizon_end_day"])           # ends at 00:00 on day 4
time_step = float(params["time_step_hours"])               # 15 minute steps

# Sets 
SHIPMENTS = list(shipments_df["shipment_id"])
TRACTORS = list(tractors_df["tractor_id"])
DAYS = list(range(1, demand_days + 1))
SHIPMENTS_ON_DAY = {d: [i for i in SHIPMENTS if int(SHIP[i]["day"]) == d]
                    for d in DAYS}

# Travel data 
MILES = {}
DRIVE = {}
for row in distance_df.itertuples(index=False):
    MILES[row.from_id, row.to_id] = float(row.distance_miles)
    DRIVE[row.from_id, row.to_id] = float(row.drive_hours)


def miles(a, b):
    """Miles from a to b (same place = 0, the sheet has no row for that)."""
    return 0.0 if a == b else MILES[a, b]


def drive_hours(a, b):
    """Driving hours from a to b (same place = 0)."""
    return 0.0 if a == b else DRIVE[a, b]


# Step 2: check the data for mistakes before using it
def check_data():
    problems = []
    for name, df, key in [("shipments", shipments_df, "shipment_id"),
                          ("facilities", facilities_df, "facility_id"),
                          ("tractors", tractors_df, "tractor_id"),
                          ("depots", depots_df, "depot_id")]:
        if df[key].duplicated().any():
            problems.append(f"duplicate IDs in {name}")

    locations = set(FAC) | set(DEPOT)
    for i in SHIPMENTS:
        s = SHIP[i]
        if s["origin_id"] not in FAC or s["destination_id"] not in FAC:
            problems.append(f"{i}: unknown facility")
        if not (s["pickup_open_hour"] < s["pickup_close_hour"]
                and s["delivery_open_hour"] < s["delivery_close_hour"]):
            problems.append(f"{i}: window not ordered")
        if min(s["weight_lb"], s["linear_feet"], s["pallets"],
               s["pickup_service_hours"], s["delivery_service_hours"]) <= 0:
            problems.append(f"{i}: non-positive demand or service time")
        if int(s["day"]) not in DAYS:
            problems.append(f"{i}: day outside 1..{demand_days}")
    for k in TRACTORS:
        t = TRACTOR[k]
        if t["home_depot_id"] not in DEPOT:
            problems.append(f"{k}: unknown depot")
        if t["equipment_id"] not in EQUIP or t["equipment_id"] not in TLCOST:
            problems.append(f"{k}: unknown equipment")
        if not t["shift_start_hour"] < t["shift_end_hour"]:
            problems.append(f"{k}: shift not ordered")
    for a in locations:
        for b in locations:
            if a != b and (a, b) not in MILES:
                problems.append(f"missing travel row {a}->{b}")
    if problems:
        raise ValueError("Data problems:\n  " + "\n  ".join(problems))


check_data()


# Step 3: parameters


# Cost of the LTL shipments 
def ltl_cost_of(i):
    s = SHIP[i]
    direct = miles(s["origin_id"], s["destination_id"])
    m = ltl_df[(ltl_df["freight_class"] == s["freight_class"])
               & (ltl_df["weight_from_lb"] <= s["weight_lb"])
               & (s["weight_lb"] < ltl_df["weight_to_lb"])
               & (ltl_df["distance_from_miles"] <= direct)
               & (direct < ltl_df["distance_to_miles"])]
    if len(m) != 1:
        raise ValueError(f"{i}: {len(m)} LTL rate matches (need exactly 1)")
    r = m.iloc[0]
    return float(r["base_cost_cad"]) * (1 + float(r["fuel_surcharge_pct"]))


ltl_cost = {i: ltl_cost_of(i) for i in SHIPMENTS}

# time windows in absolute time (24*day + hour)
# earliest_start = whichever is later: window open or facility open
# latest_finish = whichever is earlier: window close or facility close
earliest_start, latest_finish, location, service_hours = {}, {}, {}, {}
for i in SHIPMENTS:
    s = SHIP[i]
    d = int(s["day"])
    o, e = s["origin_id"], s["destination_id"]
    P, D = ("P", i), ("D", i)
    location[P], location[D] = o, e
    service_hours[P] = float(s["pickup_service_hours"])
    service_hours[D] = float(s["delivery_service_hours"])
    earliest_start[P] = 24 * d + max(s["pickup_open_hour"], FAC[o]["open_hour"])
    latest_finish[P] = 24 * d + min(s["pickup_close_hour"], FAC[o]["close_hour"])
    earliest_start[D] = 24 * d + max(s["delivery_open_hour"], FAC[e]["open_hour"])
    latest_finish[D] = 24 * d + min(s["delivery_close_hour"], FAC[e]["close_hour"])


def tour_bounds(k, d):
    """Earliest start and latest end of a tour (has to fit the shift and the depot hours)."""
    t = TRACTOR[k]
    h = DEPOT[t["home_depot_id"]]
    start = 24 * d + max(t["shift_start_hour"], h["open_hour"])
    end = 24 * d + min(t["shift_end_hour"], h["close_hour"])
    return start, min(end, 24 * horizon_end_day)        # and the horizon end


# rest between days: each truck does 1 tour a day inside a fixed shift,
# so the rest is at least 24 - shift length. we just check that here
for k in TRACTORS:
    t = TRACTOR[k]
    rest = 24 - (t["shift_end_hour"] - t["shift_start_hour"])
    if rest + EPS < min_rest_hours:
        raise ValueError(f"{k}: shifts give only {rest} h rest (C21)")

# this file only handles side access (no LIFO), like the simplified data
assert lifo_required == 0, "This file is for side access (lifo_required = 0)"


# Step 4: make every possible tour
def schedule_tour(k, d, sequence, depart, check_duty=True):
    """Simulates one tour stop by stop, for a given stop order and departure time.
    Returns the whole schedule, or None if any rule is broken.
    check_duty=False is only used on the first try (leaving as early as
    possible), because we can still cut the duty later by leaving later."""
    t = TRACTOR[k]
    home = t["home_depot_id"]
    cap = EQUIP[t["equipment_id"]]
    _, latest_end = tour_bounds(k, d)

    now, here = depart, home
    total_drive = total_miles = 0.0
    load_w = load_f = load_p = 0.0                       # truck starts empty
    stops = []
    for (kind, i) in sequence:
        u = (kind, i)
        leg_h, leg_mi = drive_hours(here, location[u]), miles(here, location[u])
        arrival = now + leg_h                            # arrival = left the last stop + driving time
        start = max(arrival, earliest_start[u])          # can't start before the window/facility opens (waiting is ok)
        finish = start + service_hours[u]
        if finish > latest_finish[u] + EPS:              # has to finish before the window/facility closes
            return None
        s = SHIP[i]
        sign = 1 if kind == "P" else -1                  # +load at a pickup, -load at a delivery
        load_w += sign * s["weight_lb"]
        load_f += sign * s["linear_feet"]
        load_p += sign * s["pallets"]
        if (load_w > cap["weight_capacity_lb"] + EPS     # check weight, feet and pallets after every stop
                or load_f > cap["length_capacity_feet"] + EPS
                or load_p > cap["pallet_capacity"] + EPS):
            return None
        total_drive += leg_h
        total_miles += leg_mi
        stops.append({"event": kind, "shipment": i, "location": location[u],
                      "leg_miles": leg_mi, "leg_drive": leg_h,
                      "arrival": arrival, "service_start": start,
                      "wait": start - arrival, "departure": finish,  # leave after the handling time
                      "load_weight": load_w, "load_feet": load_f,
                      "load_pallets": load_p})
        now, here = finish, location[u]

    back_h, back_mi = drive_hours(here, home), miles(here, home)
    total_drive += back_h
    total_miles += back_mi
    return_time = now + back_h                           # drive back to the depot
    if return_time > latest_end + EPS:                   # back before the shift ends / depot closes
        return None
    if total_drive > max_drive_hours + EPS:              # max 8 h of driving
        return None
    if check_duty and return_time - depart > max_duty_hours + EPS:   # max 12 h of duty
        return None
    if abs(load_w) + abs(load_f) + abs(load_p) > EPS:    # truck should be empty when it gets back
        return None

    c = TLCOST[t["equipment_id"]]
    n_events = len(sequence)
    linehaul = max(c["minimum_linehaul_cad"],                     # linehaul = max(minimum, miles x rate)
                   c["linehaul_cad_per_mile"] * total_miles)
    stop_fee = max(0, n_events - 2) * c["extra_stop_cad"]         # $50 for every stop after the first two
    return {"tractor": k, "day": d, "sequence": tuple(sequence),
            "shipments": tuple(sorted({i for _, i in sequence})),
            "departure": depart, "return_time": return_time,
            "miles": total_miles, "drive_hours": total_drive,
            "duty_hours": return_time - depart, "return_miles": back_mi,
            "return_drive": back_h, "linehaul": linehaul,
            "stop_fee": stop_fee, "cost": linehaul + stop_fee,
            "stops": stops}


def best_departure(k, d, sequence):
    """Picks the departure time that gives the least duty.
    Leaving as early as possible gives the earliest service times, and
    leaving later only cuts waiting time. The truck can leave later by
    min(total waiting, slack) without missing any window. All the times
    in the data are multiples of 0.25 h, so the answer is too."""
    earliest, _ = tour_bounds(k, d)
    first = schedule_tour(k, d, sequence, earliest, check_duty=False)
    if first is None:            # a rule fails even when leaving early, so this order doesn't work
        return None
    waited, slack = 0.0, float("inf")
    for st in first["stops"]:
        u = (st["event"], st["shipment"])
        waited += st["wait"]
        slack = min(slack, waited + latest_finish[u] - st["service_start"]
                    - service_hours[u])
    delay = max(0.0, min(waited, slack))
    delay = round(delay / time_step) * time_step if time_step > 0 else delay
    # schedule with the least duty, checked again with all the rules (incl. max duty)
    return schedule_tour(k, d, sequence, earliest + delay, check_duty=True)


def pickup_delivery_orders(group):
    """All the stop orders where each pickup comes before its own delivery.
    It's side access, so there's no other rule about the order."""
    orders = []

    def extend(seq, picked, delivered):
        if len(delivered) == len(group):
            orders.append(list(seq))
            return
        for i in group:
            if i not in picked:
                extend(seq + [("P", i)], picked | {i}, delivered)
            elif i not in delivered:
                extend(seq + [("D", i)], picked, delivered | {i})

    extend([], frozenset(), frozenset())
    return orders


def generate_tours():
    """For every day, every type of truck and every group of 1 to 3
    shipments, keep the cheapest stop order that works.
    Trucks with the same depot, equipment and shift are identical, so we
    only check one of them and copy the tour to the others."""
    profiles = {}
    for k in TRACTORS:
        t = TRACTOR[k]
        key = (t["home_depot_id"], t["equipment_id"],
               t["shift_start_hour"], t["shift_end_hour"])
        profiles.setdefault(key, []).append(k)

    tours = []
    max_shipments = max_stops // 2                       # 6 stops -> max 3 shipments
    for d in DAYS:                                       # tours only use shipments from the same day
        for key, members in profiles.items():
            k0 = members[0]
            for size in range(1, max_shipments + 1):
                for group in itertools.combinations(SHIPMENTS_ON_DAY[d], size):
                    best = None
                    for seq in pickup_delivery_orders(group):
                        sch = best_departure(k0, d, seq)
                        if sch is None:
                            continue
                        if (best is None or sch["cost"] < best["cost"] - EPS
                                or (abs(sch["cost"] - best["cost"]) <= EPS
                                    and sch["duty_hours"] < best["duty_hours"])):
                            best = sch
                    if best is not None:
                        for k in members:                # copy the tour to every identical truck
                            tour = dict(best, tractor=k)
                            tours.append(tour)
    return tours


t0 = time.time()
tours = generate_tours()
generation_seconds = time.time() - t0
print(f"Candidate tours generated: {len(tours)} "
      f"({generation_seconds:.1f} s; one schedule per feasible group and tractor)")


# Step 5: the Gurobi model that picks the tours
def build_selection_model(tours):
    model = gp.Model("Consolidation_Simplified")

    R = range(len(tours))
    tours_with = {i: [] for i in SHIPMENTS}
    tours_of = {(k, d): [] for k in TRACTORS for d in DAYS}
    for r, tour in enumerate(tours):
        for i in tour["shipments"]:
            tours_with[i].append(r)
        tours_of[tour["tractor"], tour["day"]].append(r)

    # Step 1: decision variables
    select_tour = model.addVars(R, vtype=GRB.BINARY, name="select_tour")
    use_ltl = model.addVars(SHIPMENTS, vtype=GRB.BINARY, name="use_ltl")

    # Step 2: objective = truck tour costs + LTL costs
    model.setObjective(
        gp.quicksum(tours[r]["cost"] * select_tour[r] for r in R)
        + gp.quicksum(ltl_cost[i] * use_ltl[i] for i in SHIPMENTS),
        GRB.MINIMIZE)

    # Step 3: constraints
    # every shipment is served exactly once (by a tour or by LTL)
    model.addConstrs(
        (gp.quicksum(select_tour[r] for r in tours_with[i]) + use_ltl[i] == 1
         for i in SHIPMENTS), name="ServeOnce")
    # each truck can do at most one tour per day
    model.addConstrs(
        (gp.quicksum(select_tour[r] for r in tours_of[k, d]) <= 1
         for (k, d) in tours_of), name="OneTourPerTractorDay")

    # solver settings (we print these in the report)
    model.Params.MIPGap = 0.0
    model.Params.TimeLimit = TIME_LIMIT_SECONDS
    model.Params.Seed = 0
    return model, select_tour, use_ltl



def greedy_pair_savings(tours, ltl_cost):
    """Take profitable two-shipment tours in descending savings order."""
    pairs = []
    for tour in tours:
        shipments = tour["shipments"]
        if len(shipments) != 2:
            continue
        savings = round(sum(ltl_cost[i] for i in shipments) - tour["cost"], 2)
        if savings > 0:
            pairs.append((-savings, tour["day"], shipments, tour["tractor"]))

    used_shipments, used_tractor_days = set(), set()
    total_savings = 0.0
    for negative_savings, day, shipments, tractor in sorted(pairs):
        slot = (tractor, day)
        if slot in used_tractor_days or used_shipments.intersection(shipments):
            continue
        used_tractor_days.add(slot)
        used_shipments.update(shipments)
        total_savings -= negative_savings
    return total_savings, len(used_shipments)


model, select_tour, use_ltl = build_selection_model(tours)
model.optimize()


# Step 6: Gurobi report and results
STATUS = {GRB.OPTIMAL: "OPTIMAL", GRB.TIME_LIMIT: "TIME_LIMIT",
          GRB.INFEASIBLE: "INFEASIBLE", GRB.INF_OR_UNBD: "INF_OR_UNBD"}

print("\n================ GUROBI REPORT ================")
print(f"Gurobi version     : {'.'.join(map(str, gp.gurobi.version()))}")
print(f"Settings           : MIPGap = {model.Params.MIPGap}, "
      f"TimeLimit = {model.Params.TimeLimit} s, Seed = {model.Params.Seed}")
print(f"Candidate tours    : {len(tours)}")
print(f"Tour generation    : {generation_seconds:.1f} s")
print(f"Gurobi runtime     : {model.Runtime:.2f} s")
print(f"Termination status : {STATUS.get(model.Status, model.Status)}")

if model.SolCount == 0:
    raise SystemExit("No solution found.")

print(f"Objective (CAD)    : {model.ObjVal:,.2f}")
print(f"Best bound (CAD)   : {model.ObjBound:,.2f}")
print(f"Optimality gap     : {model.MIPGap:.4%}")
if model.Status == GRB.OPTIMAL:
    print("Optimal over the generated candidate tours.")

chosen = [tours[r] for r in range(len(tours)) if select_tour[r].X > 0.5]
on_ltl = [i for i in SHIPMENTS if use_ltl[i].X > 0.5]

all_ltl = sum(ltl_cost.values())
savings = all_ltl - model.ObjVal
heuristic_savings, heuristic_shared = greedy_pair_savings(tours, ltl_cost)
heuristic_cost = all_ltl - heuristic_savings
print("\n================ RESULTS ================")
print(f"All-LTL baseline   : CAD {all_ltl:,.2f}")
print(f"Greedy pairs cost  : CAD {heuristic_cost:,.2f} "
      f"({heuristic_shared} shared shipments)")
print(f"Optimized cost     : CAD {model.ObjVal:,.2f}")
print(f"Savings            : CAD {savings:,.2f}  ({savings / all_ltl:.2%})")

dedicated = [t for t in chosen if len(t["shipments"]) == 1]
consolidated = [t for t in chosen if len(t["shipments"]) >= 2]
n_ded = sum(len(t["shipments"]) for t in dedicated)
n_con = sum(len(t["shipments"]) for t in consolidated)
print(f"Shipments on LTL           : {len(on_ltl)}")
print(f"Shipments on dedicated tour: {n_ded} ({len(dedicated)} tours)")
print(f"Shipments on shared tour   : {n_con} ({len(consolidated)} tours)")

# how full the trucks were on average (weighted by miles, empty depot legs included)
cap_w = sum(EQUIP[TRACTOR[t['tractor']]['equipment_id']]['weight_capacity_lb']
            * t["miles"] for t in chosen)
cap_f = sum(EQUIP[TRACTOR[t['tractor']]['equipment_id']]['length_capacity_feet']
            * t["miles"] for t in chosen)
used_w = used_f = 0.0
for t in chosen:
    for a, b in zip(t["stops"], t["stops"][1:]):         # legs between stops
        used_w += a["load_weight"] * b["leg_miles"]
        used_f += a["load_feet"] * b["leg_miles"]
total_miles = sum(t["miles"] for t in chosen)
empty_miles = sum(t["stops"][0]["leg_miles"] + t["return_miles"] for t in chosen)
if cap_w > 0:
    print(f"Weight utilization (mile-weighted) : {used_w / cap_w:.1%}")
    print(f"Floor utilization  (mile-weighted) : {used_f / cap_f:.1%}")
print(f"Total tour miles   : {total_miles:,.0f} "
      f"(of which depot approach + return: {empty_miles:,.0f})")

# Added elapsed time relative to direct driving plus delivery handling.
extra = []
for t in chosen:
    end = {(s["event"], s["shipment"]): s["departure"] for s in t["stops"]}
    for i in t["shipments"]:
        s = SHIP[i]
        direct = drive_hours(s["origin_id"], s["destination_id"])
        extra.append(end["D", i] - end["P", i] - direct - s["delivery_service_hours"])
if extra:
    print(f"Extra time vs direct trip: average {sum(extra) / len(extra):.2f} h, "
          f"max {max(extra):.2f} h")


def fmt(hours_abs):
    """Turns absolute time into 'Day d hh:mm' so it's easier to read."""
    day, rem = divmod(hours_abs, 24)
    h, m = divmod(round(rem * 60), 60)
    return f"Day {int(day)} {int(h):02d}:{int(m):02d}"


print("\n================ SELECTED TOURS ================")
for n, t in enumerate(sorted(chosen, key=lambda x: (x["day"], x["tractor"])), 1):
    k = t["tractor"]
    print(f"\nTour {n}: {k} ({TRACTOR[k]['driver_name']}), Day {t['day']}, "
          f"depot {TRACTOR[k]['home_depot_id']}, shipments {', '.join(t['shipments'])}")
    print(f"  depart {fmt(t['departure'])}  ->  return {fmt(t['return_time'])} | "
          f"{t['miles']:.0f} mi, drive {t['drive_hours']:.2f} h, "
          f"duty {t['duty_hours']:.2f} h | cost CAD {t['cost']:,.2f}")
    for s in t["stops"]:
        verb = "pick up" if s["event"] == "P" else "deliver"
        print(f"    {fmt(s['arrival'])} arrive {s['location']}, "
              f"{verb} {s['shipment']} {fmt(s['service_start'])}-"
              f"{fmt(s['departure'])[-5:]} (wait {s['wait']:.2f} h) | on board "
              f"{s['load_weight']:,.0f} lb, {s['load_feet']:.0f} ft, "
              f"{s['load_pallets']:.0f} pal")

print("\nShipments left on LTL:")
for i in on_ltl:
    print(f"  {i}: CAD {ltl_cost[i]:,.2f}")

# Step 7: save the plan to Excel (our checker will read this file)
tour_rows, stop_rows = [], []
for n, t in enumerate(sorted(chosen, key=lambda x: (x["day"], x["tractor"])), 1):
    k = t["tractor"]
    tour_rows.append({
        "tour_id": n, "tractor_id": k, "driver_name": TRACTOR[k]["driver_name"],
        "equipment_id": TRACTOR[k]["equipment_id"],
        "home_depot_id": TRACTOR[k]["home_depot_id"], "day": t["day"],
        "shipments": ",".join(t["shipments"]),
        "num_shipments": len(t["shipments"]), "num_events": len(t["sequence"]),
        "departure_abs": t["departure"], "return_abs": t["return_time"],
        "miles": t["miles"], "drive_hours": t["drive_hours"],
        "duty_hours": t["duty_hours"], "linehaul_cad": t["linehaul"],
        "stop_fee_cad": t["stop_fee"], "tour_cost_cad": t["cost"]})
    stop_rows.append({"tour_id": n, "tractor_id": k, "day": t["day"], "seq": 0,
                      "event": "DEPART", "shipment_id": "",
                      "location_id": TRACTOR[k]["home_depot_id"],
                      "arrival_abs": None, "service_start_abs": None,
                      "service_end_abs": None, "departure_abs": t["departure"],
                      "wait_hours": 0, "leg_miles": 0, "leg_drive_hours": 0,
                      "load_weight_lb": 0, "load_feet": 0, "load_pallets": 0})
    for q, s in enumerate(t["stops"], 1):
        stop_rows.append({"tour_id": n, "tractor_id": k, "day": t["day"],
                          "seq": q,
                          "event": "PICKUP" if s["event"] == "P" else "DELIVERY",
                          "shipment_id": s["shipment"], "location_id": s["location"],
                          "arrival_abs": s["arrival"],
                          "service_start_abs": s["service_start"],
                          "service_end_abs": s["departure"],
                          "departure_abs": s["departure"], "wait_hours": s["wait"],
                          "leg_miles": s["leg_miles"], "leg_drive_hours": s["leg_drive"],
                          "load_weight_lb": s["load_weight"], "load_feet": s["load_feet"],
                          "load_pallets": s["load_pallets"]})
    stop_rows.append({"tour_id": n, "tractor_id": k, "day": t["day"],
                      "seq": len(t["stops"]) + 1, "event": "RETURN",
                      "shipment_id": "", "location_id": TRACTOR[k]["home_depot_id"],
                      "arrival_abs": t["return_time"], "service_start_abs": None,
                      "service_end_abs": None, "departure_abs": None,
                      "wait_hours": 0, "leg_miles": t["return_miles"],
                      "leg_drive_hours": t["return_drive"],
                      "load_weight_lb": 0, "load_feet": 0, "load_pallets": 0})

ltl_rows = [{"shipment_id": i, "day": int(SHIP[i]["day"]), "ltl_cost_cad": ltl_cost[i]}
            for i in on_ltl]
summary_rows = [
    ("gurobi_version", ".".join(map(str, gp.gurobi.version()))),
    ("status", STATUS.get(model.Status, model.Status)),
    ("runtime_seconds", round(model.Runtime, 3)),
    ("tour_generation_seconds", round(generation_seconds, 3)),
    ("candidate_tours", len(tours)),
    ("objective_cad", round(model.ObjVal, 2)),
    ("mip_gap", model.MIPGap),
    ("all_ltl_cad", round(all_ltl, 2)),
    ("greedy_pair_cost_cad", round(heuristic_cost, 2)),
    ("greedy_pair_shared_shipments", heuristic_shared),
    ("savings_cad", round(savings, 2)),
    ("savings_pct", round(100 * savings / all_ltl, 2)),
    ("shipments_ltl", len(on_ltl)),
    ("shipments_dedicated", n_ded),
    ("shipments_consolidated", n_con),
    ("total_miles", total_miles),
]
with pd.ExcelWriter(PLAN_FILE) as writer:
    pd.DataFrame(tour_rows).to_excel(writer, sheet_name="tours", index=False)
    pd.DataFrame(stop_rows).to_excel(writer, sheet_name="stops", index=False)
    pd.DataFrame(ltl_rows).to_excel(writer, sheet_name="ltl", index=False)
    pd.DataFrame(summary_rows, columns=["item", "value"]).to_excel(
        writer, sheet_name="summary", index=False)
print(f"\nPlan written to {PLAN_FILE.name}")


# Run the independent checker on the saved plan.
subprocess.run([sys.executable, str(FOLDER / "checker-simplified.py")], check=True)
