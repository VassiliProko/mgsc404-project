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
RUN_COMPACT_CHECK = True          # also run the full model as a double check
COMPACT_TIME_LIMIT = 300          # seconds per day 
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
      f"({generation_seconds:.1f} s; full enumeration of all feasible tours)")


# =====================================================================
# STEP 5 - the Gurobi model that picks the tours
# =====================================================================
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


model, select_tour, use_ltl = build_selection_model(tours)
model.optimize()


# =====================================================================
# STEP 6 - Gurobi report and results
# =====================================================================
STATUS = {GRB.OPTIMAL: "OPTIMAL", GRB.TIME_LIMIT: "TIME_LIMIT",
          GRB.INFEASIBLE: "INFEASIBLE", GRB.INF_OR_UNBD: "INF_OR_UNBD"}

print("\n================ GUROBI REPORT ================")
print(f"Gurobi version     : {'.'.join(map(str, gp.gurobi.version()))}")
print(f"Settings           : MIPGap = {model.Params.MIPGap}, "
      f"TimeLimit = {model.Params.TimeLimit} s, Seed = {model.Params.Seed}")
print(f"Candidate tours    : {len(tours)} (all feasible tours)")
print(f"Tour generation    : {generation_seconds:.1f} s")
print(f"Gurobi runtime     : {model.Runtime:.2f} s")
print(f"Termination status : {STATUS.get(model.Status, model.Status)}")

if model.SolCount == 0:
    raise SystemExit("No solution found.")

print(f"Objective (CAD)    : {model.ObjVal:,.2f}")
print(f"Best bound (CAD)   : {model.ObjBound:,.2f}")
print(f"Optimality gap     : {model.MIPGap:.4%}")
if model.Status == GRB.OPTIMAL and model.MIPGap <= 1e-9:
    print("Every feasible tour was included, so this is a GLOBAL optimum.")

chosen = [tours[r] for r in range(len(tours)) if select_tour[r].X > 0.5]
on_ltl = [i for i in SHIPMENTS if use_ltl[i].X > 0.5]

all_ltl = sum(ltl_cost.values())
savings = all_ltl - model.ObjVal
print("\n================ RESULTS ================")
print(f"All-LTL baseline   : CAD {all_ltl:,.2f}")
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

# extra time vs driving straight there: (delivery end - pickup end) - direct drive
extra = []
for t in chosen:
    end = {(s["event"], s["shipment"]): s["departure"] for s in t["stops"]}
    for i in t["shipments"]:
        s = SHIP[i]
        direct = drive_hours(s["origin_id"], s["destination_id"])
        extra.append(end["D", i] - end["P", i] - direct)
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


# =====================================================================
# STEP 7 - save the plan to Excel (our checker will read this file)
# =====================================================================
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


# =====================================================================
# STEP 8 (optional) - double check with the full model
# Here every rule is written directly as a Gurobi constraint. It's solved
# one day at a time, and its cost should be the same as the tour-selection
# cost.
# =====================================================================
def solve_compact_model(d, time_limit, start_plan=None, fix_plan=False):
    """start_plan = (chosen tours, LTL shipments) from the tour-selection model.
    We give it to Gurobi as a starting solution (or fix it if fix_plan=True)
    so Gurobi checks the plan against every rule."""
    I = SHIPMENTS_ON_DAY[d]
    K = TRACTORS
    # stops are called "P_S001" (pickup) and "D_S001" (delivery), and the
    # depot is "START" at the beginning of the tour and "END" at the end
    STOPS = [f"P_{i}" for i in I] + [f"D_{i}" for i in I]
    START, END = "START", "END"
    kind = {u: u[0] for u in STOPS}                 # pickup or delivery
    ship_of = {u: u[2:] for u in STOPS}             # shipment id
    key = {u: (kind[u], ship_of[u]) for u in STOPS}  # key for the dictionaries from step 3

    def loc(u, k):
        return TRACTOR[k]["home_depot_id"] if u in (START, END) else location[key[u]]

    def svc(u):
        return service_hours[key[u]]

    def ev_start(u):
        return earliest_start[key[u]]

    def lt_finish(u):
        return latest_finish[key[u]]

    # legs that are allowed: START -> pickup, stop -> stop, delivery -> END
    # (the truck leaves empty so the first stop has to be a pickup, and it
    # comes back empty so the last stop has to be a delivery)
    # we also skip legs that are impossible time-wise. this doesn't remove
    # any tour that works, it just makes the model smaller
    LEGS = {}
    for k in K:
        legs = [(START, v) for v in STOPS if kind[v] == "P"]
        for u in STOPS:
            for v in STOPS:
                if u == v or (kind[u] == "D" and v == "P_" + ship_of[u]):
                    continue
                if (ev_start(u) + svc(u) + drive_hours(loc(u, k), loc(v, k))
                        > lt_finish(v) - svc(v) + EPS):
                    continue
                legs.append((u, v))
            if kind[u] == "D":
                legs.append((u, END))
        LEGS[k] = legs

    cap = {k: EQUIP[TRACTOR[k]["equipment_id"]] for k in K}
    cost = {k: TLCOST[TRACTOR[k]["equipment_id"]] for k in K}
    bounds = {k: tour_bounds(k, d) for k in K}
    M_time = 24.0                                   # big M for time = 24 h (everything happens in one day)
    n_max = max_stops // 2

    m = gp.Model(f"Compact_Day{d}")

    # ---- Step 1: decision variables ----
    use_ltl_A = m.addVars(I, vtype=GRB.BINARY, name="use_ltl")
    on_tour = m.addVars(I, K, vtype=GRB.BINARY, name="on_tour")
    tour_used = m.addVars(K, vtype=GRB.BINARY, name="tour_used")
    is_consolidated = m.addVars(K, vtype=GRB.BINARY, name="is_consolidated")
    drive_leg = m.addVars([(u, v, k) for k in K for (u, v) in LEGS[k]],
                          vtype=GRB.BINARY, name="drive_leg")
    depot_departure = m.addVars(K, name="depot_departure")
    arrival = m.addVars(STOPS, K, name="arrival")
    return_time = m.addVars(K, name="return_time")
    service_start = m.addVars(STOPS, K, name="service_start")
    stop_departure = m.addVars(STOPS, K, name="stop_departure")
    load_weight = m.addVars(STOPS, K, name="load_weight")
    load_feet = m.addVars(STOPS, K, name="load_feet")
    load_pallets = m.addVars(STOPS, K, name="load_pallets")
    linehaul_charge = m.addVars(K, name="linehaul_charge")
    stop_charge = m.addVars(K, name="stop_charge")

    out_legs = {(u, k): [] for k in K for u in STOPS + [START]}
    in_legs = {(v, k): [] for k in K for v in STOPS + [END]}
    for k in K:
        for (u, v) in LEGS[k]:
            out_legs[u, k].append(v)
            in_legs[v, k].append(u)

    # ---- Step 2: objective ----
    m.setObjective(gp.quicksum(linehaul_charge[k] + stop_charge[k] for k in K)
                   + gp.quicksum(ltl_cost[i] * use_ltl_A[i] for i in I),
                   GRB.MINIMIZE)

    # ---- Step 3: constraints ----
    n_ship = {k: gp.quicksum(on_tour[i, k] for i in I) for k in K}
    # each shipment served exactly once (only this day's shipments are in the model)
    m.addConstrs((on_tour.sum(i, "*") + use_ltl_A[i] == 1 for i in I), name="C1")
    # a shipment can only be on a tour that's used
    m.addConstrs((on_tour[i, k] <= tour_used[k] for i in I for k in K), name="C3")
    # a used tour carries at least 1 shipment
    m.addConstrs((tour_used[k] <= n_ship[k] for k in K), name="C4")
    # consolidated tour = 2 or more shipments
    m.addConstrs((n_ship[k] >= 2 * is_consolidated[k] for k in K), name="C5a")
    m.addConstrs((n_ship[k] <= tour_used[k] + (n_max - 1) * is_consolidated[k]
                  for k in K), name="C5b")
    # if the truck is used, it leaves its depot once and comes back once
    m.addConstrs((gp.quicksum(drive_leg[START, v, k] for v in out_legs[START, k])
                  == tour_used[k] for k in K), name="C6")
    m.addConstrs((gp.quicksum(drive_leg[u, END, k] for u in in_legs[END, k])
                  == tour_used[k] for k in K), name="C7")
    # flow: the truck goes in and out of each of its stops once (same truck for pickup and delivery)
    m.addConstrs((gp.quicksum(drive_leg[u, v, k] for v in out_legs[u, k])
                  == on_tour[ship_of[u], k] for u in STOPS for k in K), name="C8out")
    m.addConstrs((gp.quicksum(drive_leg[v, u, k] for v in in_legs[u, k])
                  == on_tour[ship_of[u], k] for u in STOPS for k in K), name="C8in")
    # only the legs in LEGS[k] exist (no depot in the middle, no empty tours)

    for k in K:
        for (u, v) in LEGS[k]:
            leave_u = depot_departure[k] if u == START else stop_departure[u, k]
            reach_v = return_time[k] if v == END else arrival[v, k]
            # timing: arrival = departure + driving time if the leg is used (big M)
            m.addConstr(reach_v >= leave_u + drive_hours(loc(u, k), loc(v, k))
                        - 2 * M_time * (1 - drive_leg[u, v, k]),
                        name=f"C10[{u},{v},{k}]")
            # loads after each stop (weight, feet, pallets)
            if v != END:
                s = SHIP[ship_of[v]]
                sgn = 1 if kind[v] == "P" else -1
                for var, dem, capv in [(load_weight, s["weight_lb"], cap[k]["weight_capacity_lb"]),
                                       (load_feet, s["linear_feet"], cap[k]["length_capacity_feet"]),
                                       (load_pallets, s["pallets"], cap[k]["pallet_capacity"])]:
                    before = 0 if u == START else var[u, k]     # truck starts empty
                    m.addConstr(var[v, k] >= before + sgn * dem
                                - 2 * capv * (1 - drive_leg[u, v, k]),
                                name=f"C26[{u},{v},{k}]")

    for u in STOPS:
        for k in K:
            i = ship_of[u]
            # can't start before arriving (waiting is ok)
            m.addConstr(service_start[u, k] >= arrival[u, k], name=f"C11[{u},{k}]")
            # leave after the handling is done
            m.addConstr(stop_departure[u, k] >= service_start[u, k] + svc(u),
                        name=f"C12[{u},{k}]")
            # not before the window and the facility open
            m.addConstr(service_start[u, k] >= ev_start(u), name=f"C13[{u},{k}]")
            # done before the window and the facility close
            m.addConstr(service_start[u, k] + svc(u)
                        <= lt_finish(u) + M_time * (1 - on_tour[i, k]),
                        name=f"C14[{u},{k}]")
            # capacity limits after every stop
            load_weight[u, k].UB = cap[k]["weight_capacity_lb"]
            load_feet[u, k].UB = cap[k]["length_capacity_feet"]
            load_pallets[u, k].UB = cap[k]["pallet_capacity"]

    for i in I:
        for k in K:
            # pickup before delivery
            m.addConstr(service_start["D_" + i, k] >= service_start["P_" + i, k]
                        + svc("P_" + i) - M_time * (1 - on_tour[i, k]),
                        name=f"C15[{i},{k}]")

    for k in K:
        earliest, latest = bounds[k]
        # leave after the shift starts and the depot opens
        m.addConstr(depot_departure[k] >= earliest, name=f"C16[{k}]")
        # back before the shift ends, the depot closes and the horizon ends
        m.addConstr(return_time[k] <= latest, name=f"C17_C18[{k}]")
        # max 8 h of driving
        m.addConstr(gp.quicksum(drive_hours(loc(u, k), loc(v, k)) * drive_leg[u, v, k]
                                for (u, v) in LEGS[k])
                    <= max_drive_hours * tour_used[k], name=f"C19[{k}]")
        # max 12 h of duty (return - departure), and duty can't be negative
        m.addConstr(return_time[k] - depot_departure[k] <= max_duty_hours * tour_used[k],
                    name=f"C20[{k}]")
        m.addConstr(return_time[k] >= depot_departure[k], name=f"C20b[{k}]")
        # max 6 stops
        m.addConstr(2 * n_ship[k] <= max_stops * tour_used[k], name=f"C29[{k}]")
        # linehaul = max(minimum, miles x rate)
        m.addConstr(linehaul_charge[k] >= cost[k]["minimum_linehaul_cad"] * tour_used[k],
                    name=f"C30a[{k}]")
        m.addConstr(linehaul_charge[k] >= cost[k]["linehaul_cad_per_mile"]
                    * gp.quicksum(miles(loc(u, k), loc(v, k)) * drive_leg[u, v, k]
                                  for (u, v) in LEGS[k]), name=f"C30b[{k}]")
        # stop fee = max(0, stops - 2) x 50
        m.addConstr(stop_charge[k] >= cost[k]["extra_stop_cad"]
                    * (2 * n_ship[k] - 2 * tour_used[k]), name=f"C31[{k}]")
    # rest between days: already guaranteed by the shifts (we checked it in step 3)
    # binary / non-negative variables were already set when we created them

    # starting solution: the plan from the tour-selection model
    if start_plan is not None:
        m.update()                       # need this before we can use the variables as keys
        plan_tours, plan_ltl = start_plan
        starts = {}
        for i in I:
            starts[use_ltl_A[i]] = 1 if i in plan_ltl else 0
            for k in K:
                starts[on_tour[i, k]] = 0
        for k in K:
            starts[tour_used[k]] = starts[is_consolidated[k]] = 0
        for var in drive_leg.values():
            starts[var] = 0
        for t in plan_tours:
            if t["day"] != d:
                continue
            k = t["tractor"]
            starts[tour_used[k]] = 1
            starts[is_consolidated[k]] = 1 if len(t["shipments"]) >= 2 else 0
            for i in t["shipments"]:
                starts[on_tour[i, k]] = 1
            path = [START] + [f"{e}_{i}" for e, i in t["sequence"]] + [END]
            for u, v in zip(path, path[1:]):
                starts[drive_leg[u, v, k]] = 1
        for var, val in starts.items():
            if fix_plan:
                var.LB = var.UB = val
            else:
                var.Start = val

    m.Params.OutputFlag = 0
    m.Params.TimeLimit = time_limit
    m.Params.MIPGap = 0.0
    m.optimize()
    return m


if RUN_COMPACT_CHECK:
    print("\n================ CROSS-CHECK: COMPACT MILP (Formulation A) ================")
    print("The tour-selection plan is given to the compact model (all constraints")
    print("C1–C31) as a starting solution; Gurobi then searches for anything cheaper.")
    for d in DAYS:
        day_cost_B = (sum(t["cost"] for t in chosen if t["day"] == d)
                      + sum(ltl_cost[i] for i in on_ltl if int(SHIP[i]["day"]) == d))
        mA = solve_compact_model(d, COMPACT_TIME_LIMIT, start_plan=(chosen, on_ltl))
        status_A = STATUS.get(mA.Status, mA.Status)
        if mA.SolCount == 0:
            print(f"Day {d}: compact model found no solution ({status_A})")
            continue
        print(f"Day {d}: tour selection CAD {day_cost_B:,.2f} | compact MILP best "
              f"CAD {mA.ObjVal:,.2f}, bound CAD {mA.ObjBound:,.2f} "
              f"({status_A}, {mA.Runtime:.0f} s)")
        if mA.ObjVal < day_cost_B - 0.01:
            print("   -> WARNING: compact model found a cheaper plan; a tour is "
                  "missing from the candidate list.")
        elif mA.Status == GRB.OPTIMAL:
            print("   -> MATCH: compact model proves the same optimal cost.")
        else:
            print("   -> CONSISTENT: plan accepted by the compact model and nothing "
                  "cheaper found in the time limit (bound <= cost).")