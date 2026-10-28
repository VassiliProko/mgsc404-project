# =====================================================================
#  MGSC 404 – Project 1: Consolidating Partial Loads into Full Truckloads
#  Gurobi model – SIMPLIFIED workbook (project1_simplified.xlsx)
#
#  Constraint and variable IDs (C1–C31, V1–V18, B1–B4) refer to
#  README_simplified.md.
#
#  How the program works (same idea as the set-cover model in Module 3):
#    1. Read the workbook.
#    2. Generate EVERY feasible truck tour (1, 2 or 3 shipments, every
#       valid stop order, every tractor). A tour is kept only if it
#       passes C2 and C6–C31 (routing, timing, hours of service,
#       capacity, stop limit, cost).
#    3. Gurobi tour-selection model (Formulation B):
#         variables   select_tour[r], use_ltl[i]          (binary)
#         objective   min  Σ tour_cost·select_tour + Σ ltl_cost·use_ltl
#         constraints B1  every shipment served exactly once
#                     B2  at most one tour per tractor per day
#    4. Print the Gurobi report and export the plan to Excel.
#    5. (Optional) Cross-check: solve the full compact MILP
#       (Formulation A, constraints C1–C31 written as Gurobi
#       constraints) day by day and compare the cost.
#
#  Run:  python consolidation_simplified.py
#        (the .xlsx file must be in the same folder as this script)
# =====================================================================

import itertools
import time
from pathlib import Path

import pandas as pd
import gurobipy as gp
from gurobipy import GRB

# ---------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------
FOLDER = Path(__file__).resolve().parent
DATA_FILE = FOLDER / "project1_simplified.xlsx"
PLAN_FILE = FOLDER / "plan_simplified.xlsx"

TIME_LIMIT_SECONDS = 600          # Gurobi time limit (tour-selection model)
RUN_COMPACT_CHECK = True          # also solve Formulation A day by day
COMPACT_TIME_LIMIT = 300          # seconds per day for Formulation A
EPS = 1e-6                        # tolerance for comparing hours / loads


# =====================================================================
# STEP 1 – READ THE WORKBOOK
# =====================================================================
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

# Dictionaries keyed by ID (README §2: values are read, never hard-coded)
SHIP = shipments_df.set_index("shipment_id").to_dict("index")
FAC = facilities_df.set_index("facility_id").to_dict("index")
DEPOT = depots_df.set_index("depot_id").to_dict("index")
EQUIP = equipment_df.set_index("equipment_id").to_dict("index")
TLCOST = tl_costs_df.set_index("equipment_id").to_dict("index")
TRACTOR = tractors_df.set_index("tractor_id").to_dict("index")

rules = sheets["driver_rules"].set_index("parameter")["value"].to_dict()
params = sheets["parameters"].set_index("parameter")["value"].to_dict()

max_drive_hours = float(rules["max_drive_hours"])          # 8
max_duty_hours = float(rules["max_duty_hours"])            # 12
min_rest_hours = float(rules["min_rest_hours"])            # 12
max_stops = int(params["max_stops"])                       # 6
lifo_required = int(params["lifo_required"])               # 0
demand_days = int(params["demand_days"])                   # 3
horizon_end_day = int(params["horizon_end_day"])           # 4
time_step = float(params["time_step_hours"])               # 0.25

# Sets (README §1)
SHIPMENTS = list(shipments_df["shipment_id"])
TRACTORS = list(tractors_df["tractor_id"])
DAYS = list(range(1, demand_days + 1))
SHIPMENTS_ON_DAY = {d: [i for i in SHIPMENTS if int(SHIP[i]["day"]) == d]
                    for d in DAYS}

# Travel data (directed, README §2.1)
MILES = {}
DRIVE = {}
for row in distance_df.itertuples(index=False):
    MILES[row.from_id, row.to_id] = float(row.distance_miles)
    DRIVE[row.from_id, row.to_id] = float(row.drive_hours)


def miles(a, b):
    """Directed miles; same location = 0 (README p.2: no row for it)."""
    return 0.0 if a == b else MILES[a, b]


def drive_hours(a, b):
    """Directed stored driving hours; same location = 0."""
    return 0.0 if a == b else DRIVE[a, b]


# =====================================================================
# STEP 2 – DATA CHECKS (data README §6)
# =====================================================================
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


# =====================================================================
# STEP 3 – PARAMETERS
# =====================================================================
# LTL cost (README §2.5): exactly one matching band per shipment.
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

# Time windows in absolute time = 24·day + hour  (C13, C14)
# earliest_start = max(window open, facility open)
# latest_finish  = min(window close, facility close)
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
    """C16, C17: tour must fit the driver shift AND the depot hours."""
    t = TRACTOR[k]
    h = DEPOT[t["home_depot_id"]]
    start = 24 * d + max(t["shift_start_hour"], h["open_hour"])
    end = 24 * d + min(t["shift_end_hour"], h["close_hour"])
    return start, min(end, 24 * horizon_end_day)        # C18 horizon


# C21 (rest between daily tours). With one tour per day inside fixed
# repeating shifts, the rest is at least 24 − (shift length). Checked here.
for k in TRACTORS:
    t = TRACTOR[k]
    rest = 24 - (t["shift_end_hour"] - t["shift_start_hour"])
    if rest + EPS < min_rest_hours:
        raise ValueError(f"{k}: shifts give only {rest} h rest (C21)")

# The simplified rules this code implements (README §8.1)
assert lifo_required == 0, "This file is for side access (lifo_required = 0)"


# =====================================================================
# STEP 4 – GENERATE ALL FEASIBLE TOURS  (enforces C2, C6–C31 per tour)
# =====================================================================
def schedule_tour(k, d, sequence, depart, check_duty=True):
    """Forward pass for a fixed stop sequence and departure time.
    Returns the full schedule, or None if a rule is broken.
    check_duty=False is used only for the first (earliest-departure)
    pass, because duty can still be cut by leaving later."""
    t = TRACTOR[k]
    home = t["home_depot_id"]
    cap = EQUIP[t["equipment_id"]]
    _, latest_end = tour_bounds(k, d)

    now, here = depart, home
    total_drive = total_miles = 0.0
    load_w = load_f = load_p = 0.0                       # C28: empty at start
    stops = []
    for (kind, i) in sequence:
        u = (kind, i)
        leg_h, leg_mi = drive_hours(here, location[u]), miles(here, location[u])
        arrival = now + leg_h                            # C10
        start = max(arrival, earliest_start[u])          # C11, C13
        finish = start + service_hours[u]
        if finish > latest_finish[u] + EPS:              # C14
            return None
        s = SHIP[i]
        sign = 1 if kind == "P" else -1                  # C26
        load_w += sign * s["weight_lb"]
        load_f += sign * s["linear_feet"]
        load_p += sign * s["pallets"]
        if (load_w > cap["weight_capacity_lb"] + EPS     # C27
                or load_f > cap["length_capacity_feet"] + EPS
                or load_p > cap["pallet_capacity"] + EPS):
            return None
        total_drive += leg_h
        total_miles += leg_mi
        stops.append({"event": kind, "shipment": i, "location": location[u],
                      "leg_miles": leg_mi, "leg_drive": leg_h,
                      "arrival": arrival, "service_start": start,
                      "wait": start - arrival, "departure": finish,  # C12
                      "load_weight": load_w, "load_feet": load_f,
                      "load_pallets": load_p})
        now, here = finish, location[u]

    back_h, back_mi = drive_hours(here, home), miles(here, home)
    total_drive += back_h
    total_miles += back_mi
    return_time = now + back_h                           # C10 into end depot
    if return_time > latest_end + EPS:                   # C17, C18
        return None
    if total_drive > max_drive_hours + EPS:              # C19
        return None
    if check_duty and return_time - depart > max_duty_hours + EPS:   # C20
        return None
    if abs(load_w) + abs(load_f) + abs(load_p) > EPS:    # C28: empty at return
        return None

    c = TLCOST[t["equipment_id"]]
    n_events = len(sequence)
    linehaul = max(c["minimum_linehaul_cad"],                     # C30
                   c["linehaul_cad_per_mile"] * total_miles)
    stop_fee = max(0, n_events - 2) * c["extra_stop_cad"]         # C31
    return {"tractor": k, "day": d, "sequence": tuple(sequence),
            "shipments": tuple(sorted({i for _, i in sequence})),
            "departure": depart, "return_time": return_time,
            "miles": total_miles, "drive_hours": total_drive,
            "duty_hours": return_time - depart, "return_miles": back_mi,
            "return_drive": back_h, "linehaul": linehaul,
            "stop_fee": stop_fee, "cost": linehaul + stop_fee,
            "stops": stops}


def best_departure(k, d, sequence):
    """Choose the departure that gives the least duty (C16, C20).
    Leaving at the earliest time gives the earliest possible service
    times. Leaving later only removes waiting. The truck can leave
    later by   min(total waiting, forward slack)   without missing any
    window (forward time-slack rule). Every input is a multiple of
    0.25 h, so the result is on the 0.25 h grid."""
    earliest, _ = tour_bounds(k, d)
    first = schedule_tour(k, d, sequence, earliest, check_duty=False)
    if first is None:            # a window, capacity, drive or return rule fails
        return None
    waited, slack = 0.0, float("inf")
    for st in first["stops"]:
        u = (st["event"], st["shipment"])
        waited += st["wait"]
        slack = min(slack, waited + latest_finish[u] - st["service_start"]
                    - service_hours[u])
    delay = max(0.0, min(waited, slack))
    delay = round(delay / time_step) * time_step if time_step > 0 else delay
    # Least-duty schedule; it is re-checked with every rule, including C20.
    return schedule_tour(k, d, sequence, earliest + delay, check_duty=True)


def pickup_delivery_orders(group):
    """All event orders in which each pickup comes before its own
    delivery (C15). Side access: no other order rule (README §5.8)."""
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
    """For every day, tractor profile and group of 1..max_stops/2
    shipments (C29), keep the cheapest feasible stop order.
    Tractors with the same depot, equipment and shift are identical,
    so each profile is evaluated once and copied to its tractors."""
    profiles = {}
    for k in TRACTORS:
        t = TRACTOR[k]
        key = (t["home_depot_id"], t["equipment_id"],
               t["shift_start_hour"], t["shift_end_hour"])
        profiles.setdefault(key, []).append(k)

    tours = []
    max_shipments = max_stops // 2                       # C29
    for d in DAYS:                                       # C2: same-day tours
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
                        for k in members:                # copy to each tractor
                            tour = dict(best, tractor=k)
                            tours.append(tour)
    return tours


t0 = time.time()
tours = generate_tours()
generation_seconds = time.time() - t0
print(f"Candidate tours generated: {len(tours)} "
      f"({generation_seconds:.1f} s; full enumeration of all feasible tours)")


# =====================================================================
# STEP 5 – GUROBI TOUR-SELECTION MODEL (Formulation B)
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

    # Step 1 – decision variables (B-V1, B-V2)
    select_tour = model.addVars(R, vtype=GRB.BINARY, name="select_tour")
    use_ltl = model.addVars(SHIPMENTS, vtype=GRB.BINARY, name="use_ltl")

    # Step 2 – objective: TL tour costs + LTL costs
    model.setObjective(
        gp.quicksum(tours[r]["cost"] * select_tour[r] for r in R)
        + gp.quicksum(ltl_cost[i] * use_ltl[i] for i in SHIPMENTS),
        GRB.MINIMIZE)

    # Step 3 – constraints
    # B1: every shipment is served exactly once (truck tour or LTL)
    model.addConstrs(
        (gp.quicksum(select_tour[r] for r in tours_with[i]) + use_ltl[i] == 1
         for i in SHIPMENTS), name="ServeOnce")
    # B2: at most one tour per tractor per day
    model.addConstrs(
        (gp.quicksum(select_tour[r] for r in tours_of[k, d]) <= 1
         for (k, d) in tours_of), name="OneTourPerTractorDay")

    # Settings that matter (reported below)
    model.Params.MIPGap = 0.0
    model.Params.TimeLimit = TIME_LIMIT_SECONDS
    model.Params.Seed = 0
    return model, select_tour, use_ltl


model, select_tour, use_ltl = build_selection_model(tours)
model.optimize()


# =====================================================================
# STEP 6 – GUROBI REPORT AND RESULTS
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

# Utilization (mile-weighted, all legs including empty depot legs)
cap_w = sum(EQUIP[TRACTOR[t['tractor']]['equipment_id']]['weight_capacity_lb']
            * t["miles"] for t in chosen)
cap_f = sum(EQUIP[TRACTOR[t['tractor']]['equipment_id']]['length_capacity_feet']
            * t["miles"] for t in chosen)
used_w = used_f = 0.0
for t in chosen:
    for a, b in zip(t["stops"], t["stops"][1:]):         # loaded legs
        used_w += a["load_weight"] * b["leg_miles"]
        used_f += a["load_feet"] * b["leg_miles"]
total_miles = sum(t["miles"] for t in chosen)
empty_miles = sum(t["stops"][0]["leg_miles"] + t["return_miles"] for t in chosen)
if cap_w > 0:
    print(f"Weight utilization (mile-weighted) : {used_w / cap_w:.1%}")
    print(f"Floor utilization  (mile-weighted) : {used_f / cap_f:.1%}")
print(f"Total tour miles   : {total_miles:,.0f} "
      f"(of which depot approach + return: {empty_miles:,.0f})")

# Extra time vs. direct trip: (delivery end − pickup end) − direct drive
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
    """Absolute time -> 'Day d hh:mm'."""
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
# STEP 7 – EXPORT THE PLAN (read later by the independent checker)
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
# STEP 8 (OPTIONAL) – CROSS-CHECK WITH THE COMPACT MILP (Formulation A)
#   Every constraint C1–C31 of README_simplified.md written as a
#   Gurobi constraint, solved separately for each day (the model splits
#   by day, README §5.10). Its cost must equal the tour-selection cost.
# =====================================================================
def solve_compact_model(d, time_limit, start_plan=None, fix_plan=False):
    """start_plan = (selected tours, LTL shipments) from Formulation B.
    It is given to Gurobi as a MIP start (or fixed, if fix_plan=True),
    so Gurobi checks the plan against every constraint C1–C31."""
    I = SHIPMENTS_ON_DAY[d]
    K = TRACTORS
    # Stops are named "P_S001" (pickup) and "D_S001" (delivery); the two
    # depot copies are "START" and "END".
    STOPS = [f"P_{i}" for i in I] + [f"D_{i}" for i in I]
    START, END = "START", "END"
    kind = {u: u[0] for u in STOPS}                 # "P" or "D"
    ship_of = {u: u[2:] for u in STOPS}             # shipment id
    key = {u: (kind[u], ship_of[u]) for u in STOPS}  # key into Step-3 dicts

    def loc(u, k):
        return TRACTOR[k]["home_depot_id"] if u in (START, END) else location[key[u]]

    def svc(u):
        return service_hours[key[u]]

    def ev_start(u):
        return earliest_start[key[u]]

    def lt_finish(u):
        return latest_finish[key[u]]

    # LEGS[k] (C9): START->pickup, stop->stop, delivery->END.
    # (A truck leaves empty, so its first stop is a pickup; it returns
    #  empty, so its last stop is a delivery.)
    # Preprocessing: drop legs that can never be driven in time
    # (earliest finish at u + drive > latest start at v). This removes
    # no feasible tour; it only makes the model smaller.
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
    M_time = 24.0                                   # all times lie inside one day
    n_max = max_stops // 2

    m = gp.Model(f"Compact_Day{d}")

    # ---------------- Step 1 – decision variables ----------------
    use_ltl_A = m.addVars(I, vtype=GRB.BINARY, name="use_ltl")                    # V1
    on_tour = m.addVars(I, K, vtype=GRB.BINARY, name="on_tour")                   # V2
    tour_used = m.addVars(K, vtype=GRB.BINARY, name="tour_used")                  # V3
    is_consolidated = m.addVars(K, vtype=GRB.BINARY, name="is_consolidated")      # V4
    drive_leg = m.addVars([(u, v, k) for k in K for (u, v) in LEGS[k]],
                          vtype=GRB.BINARY, name="drive_leg")                     # V5
    depot_departure = m.addVars(K, name="depot_departure")                        # V6
    arrival = m.addVars(STOPS, K, name="arrival")                                 # V7
    return_time = m.addVars(K, name="return_time")                                # V7
    service_start = m.addVars(STOPS, K, name="service_start")                     # V8
    stop_departure = m.addVars(STOPS, K, name="stop_departure")                   # V9
    load_weight = m.addVars(STOPS, K, name="load_weight")                         # V10
    load_feet = m.addVars(STOPS, K, name="load_feet")                             # V11
    load_pallets = m.addVars(STOPS, K, name="load_pallets")                       # V12
    linehaul_charge = m.addVars(K, name="linehaul_charge")                        # V17
    stop_charge = m.addVars(K, name="stop_charge")                                # V18

    out_legs = {(u, k): [] for k in K for u in STOPS + [START]}
    in_legs = {(v, k): [] for k in K for v in STOPS + [END]}
    for k in K:
        for (u, v) in LEGS[k]:
            out_legs[u, k].append(v)
            in_legs[v, k].append(u)

    # ---------------- Step 2 – objective ----------------
    m.setObjective(gp.quicksum(linehaul_charge[k] + stop_charge[k] for k in K)
                   + gp.quicksum(ltl_cost[i] * use_ltl_A[i] for i in I),
                   GRB.MINIMIZE)

    # ---------------- Step 3 – constraints ----------------
    n_ship = {k: gp.quicksum(on_tour[i, k] for i in I) for k in K}
    # C1 served exactly once   (C2: only day-d shipments are in this model)
    m.addConstrs((on_tour.sum(i, "*") + use_ltl_A[i] == 1 for i in I), name="C1")
    # C3 shipment only on a selected tour
    m.addConstrs((on_tour[i, k] <= tour_used[k] for i in I for k in K), name="C3")
    # C4 a selected tour carries at least one shipment
    m.addConstrs((tour_used[k] <= n_ship[k] for k in K), name="C4")
    # C5 consolidated <=> at least 2 shipments
    m.addConstrs((n_ship[k] >= 2 * is_consolidated[k] for k in K), name="C5a")
    m.addConstrs((n_ship[k] <= tour_used[k] + (n_max - 1) * is_consolidated[k]
                  for k in K), name="C5b")
    # C6, C7 leave and return to the home depot once if the tour is used
    m.addConstrs((gp.quicksum(drive_leg[START, v, k] for v in out_legs[START, k])
                  == tour_used[k] for k in K), name="C6")
    m.addConstrs((gp.quicksum(drive_leg[u, END, k] for u in in_legs[END, k])
                  == tour_used[k] for k in K), name="C7")
    # C8 flow: each stop of an assigned shipment entered and left once, same tractor
    m.addConstrs((gp.quicksum(drive_leg[u, v, k] for v in out_legs[u, k])
                  == on_tour[ship_of[u], k] for u in STOPS for k in K), name="C8out")
    m.addConstrs((gp.quicksum(drive_leg[v, u, k] for v in in_legs[u, k])
                  == on_tour[ship_of[u], k] for u in STOPS for k in K), name="C8in")
    # C9 legs exist only in LEGS[k] (no depot in the middle, no empty tour)

    for k in K:
        for (u, v) in LEGS[k]:
            leave_u = depot_departure[k] if u == START else stop_departure[u, k]
            reach_v = return_time[k] if v == END else arrival[v, k]
            # C10 arrival = departure + stored driving time (Big-M)
            m.addConstr(reach_v >= leave_u + drive_hours(loc(u, k), loc(v, k))
                        - 2 * M_time * (1 - drive_leg[u, v, k]),
                        name=f"C10[{u},{v},{k}]")
            # C26 loads after each event (weight, feet, pallets)
            if v != END:
                s = SHIP[ship_of[v]]
                sgn = 1 if kind[v] == "P" else -1
                for var, dem, capv in [(load_weight, s["weight_lb"], cap[k]["weight_capacity_lb"]),
                                       (load_feet, s["linear_feet"], cap[k]["length_capacity_feet"]),
                                       (load_pallets, s["pallets"], cap[k]["pallet_capacity"])]:
                    before = 0 if u == START else var[u, k]     # C28 empty at start
                    m.addConstr(var[v, k] >= before + sgn * dem
                                - 2 * capv * (1 - drive_leg[u, v, k]),
                                name=f"C26[{u},{v},{k}]")

    for u in STOPS:
        for k in K:
            i = ship_of[u]
            # C11 service starts after arrival (waiting allowed)
            m.addConstr(service_start[u, k] >= arrival[u, k], name=f"C11[{u},{k}]")
            # C12 leave after full handling
            m.addConstr(stop_departure[u, k] >= service_start[u, k] + svc(u),
                        name=f"C12[{u},{k}]")
            # C13 not before window open and facility open
            m.addConstr(service_start[u, k] >= ev_start(u), name=f"C13[{u},{k}]")
            # C14 finish by window close and facility close
            m.addConstr(service_start[u, k] + svc(u)
                        <= lt_finish(u) + M_time * (1 - on_tour[i, k]),
                        name=f"C14[{u},{k}]")
            # C27 capacity after every stop
            load_weight[u, k].UB = cap[k]["weight_capacity_lb"]
            load_feet[u, k].UB = cap[k]["length_capacity_feet"]
            load_pallets[u, k].UB = cap[k]["pallet_capacity"]

    for i in I:
        for k in K:
            # C15 pickup before delivery
            m.addConstr(service_start["D_" + i, k] >= service_start["P_" + i, k]
                        + svc("P_" + i) - M_time * (1 - on_tour[i, k]),
                        name=f"C15[{i},{k}]")

    for k in K:
        earliest, latest = bounds[k]
        # C16 depart after shift start and depot opening
        m.addConstr(depot_departure[k] >= earliest, name=f"C16[{k}]")
        # C17, C18 return by shift end, depot closing and horizon end
        m.addConstr(return_time[k] <= latest, name=f"C17_C18[{k}]")
        # C19 max driving per tour
        m.addConstr(gp.quicksum(drive_hours(loc(u, k), loc(v, k)) * drive_leg[u, v, k]
                                for (u, v) in LEGS[k])
                    <= max_drive_hours * tour_used[k], name=f"C19[{k}]")
        # C20 max duty (departure to return); C20b duty is not negative
        m.addConstr(return_time[k] - depot_departure[k] <= max_duty_hours * tour_used[k],
                    name=f"C20[{k}]")
        m.addConstr(return_time[k] >= depot_departure[k], name=f"C20b[{k}]")
        # C29 stop limit
        m.addConstr(2 * n_ship[k] <= max_stops * tour_used[k], name=f"C29[{k}]")
        # C30 linehaul = max(minimum, miles x rate)
        m.addConstr(linehaul_charge[k] >= cost[k]["minimum_linehaul_cad"] * tour_used[k],
                    name=f"C30a[{k}]")
        m.addConstr(linehaul_charge[k] >= cost[k]["linehaul_cad_per_mile"]
                    * gp.quicksum(miles(loc(u, k), loc(v, k)) * drive_leg[u, v, k]
                                  for (u, v) in LEGS[k]), name=f"C30b[{k}]")
        # C31 stop-off = max(0, events - 2) x fee
        m.addConstr(stop_charge[k] >= cost[k]["extra_stop_cad"]
                    * (2 * n_ship[k] - 2 * tour_used[k]), name=f"C31[{k}]")
    # C21 rest between days: guaranteed by the fixed shifts (checked in Step 3).
    # C37, C38 domains: set by vtype=BINARY / default lb=0 above.

    # MIP start: the plan from the tour-selection model (all binaries)
    if start_plan is not None:
        m.update()                       # variables must exist before use as keys
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