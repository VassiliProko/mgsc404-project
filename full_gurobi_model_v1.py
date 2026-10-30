# Full Gurobi model 
#
# In this version Gurobi decides everything by itself, which shipments
# go on which truck, the order of the stops, the times at each stop and
# how full the truck is. All the constraints from our README
# are written as Gurobi constraints. Python just reads the Excel file,
# prints the results and saves the plan.


from pathlib import Path

import pandas as pd
import gurobipy as gp
from gurobipy import GRB

# Settings
FOLDER = Path(__file__).resolve().parent
DATA_FILE = FOLDER /  "data" / "project1_simplified.xlsx"
OUTPUT_FOLDER = FOLDER / "output"
OUTPUT_FOLDER.mkdir(exist_ok=True)          # creates the folder if it doesn't exist yet
PLAN_FILE = OUTPUT_FOLDER / "plan_full_gurobi.xlsx"

ONE_MODEL_PER_DAY = True      # True = solve each day on its own (same answer, faster)
                              # False = one big model for all 3 days (includes the rest-between-days rule)
TIME_LIMIT_SECONDS =  3 * 3600      # time limit for each model (3 hours)
USE_SYMMETRY_BREAKING = True  # makes identical trucks get used in order (faster)
EPS = 1e-6


# STEP 1 - read the Excel file
sheets = pd.read_excel(DATA_FILE, sheet_name=None)
for name in ["shipments", "facilities", "distance_matrix", "ltl_rates", "tl_costs",
             "equipment", "tractors", "depots", "driver_rules", "parameters"]:
    if name not in sheets:
        raise ValueError(f"Sheet '{name}' is missing from {DATA_FILE.name}")

shipments_df = sheets["shipments"]
ltl_df = sheets["ltl_rates"]
SHIP = shipments_df.set_index("shipment_id").to_dict("index")
FAC = sheets["facilities"].set_index("facility_id").to_dict("index")
DEPOT = sheets["depots"].set_index("depot_id").to_dict("index")
EQUIP = sheets["equipment"].set_index("equipment_id").to_dict("index")
TLCOST = sheets["tl_costs"].set_index("equipment_id").to_dict("index")
TRACTOR = sheets["tractors"].set_index("tractor_id").to_dict("index")
rules = sheets["driver_rules"].set_index("parameter")["value"].to_dict()
params = sheets["parameters"].set_index("parameter")["value"].to_dict()

max_drive_hours = float(rules["max_drive_hours"])      # 8 h in the simplified file
max_duty_hours = float(rules["max_duty_hours"])        # 12 h
min_rest_hours = float(rules["min_rest_hours"])        # 12 h rest between days
max_stops = int(params["max_stops"])                   # 6 stops -> max 3 shipments
lifo_required = int(params["lifo_required"])           # 0 = side access, no LIFO here
demand_days = int(params["demand_days"])               # days 1-3
horizon_end_day = int(params["horizon_end_day"])       # horizon ends at 00:00 on day 4
assert lifo_required == 0, "This file is for side access (lifo_required = 0)"

# sets (same names as in our README)
SHIPMENTS = list(shipments_df["shipment_id"])
TRACTORS = list(sheets["tractors"]["tractor_id"])
DAYS = list(range(1, demand_days + 1))
SHIPMENTS_ON_DAY = {d: [i for i in SHIPMENTS if int(SHIP[i]["day"]) == d] for d in DAYS}

MILES, DRIVE = {}, {}
for row in sheets["distance_matrix"].itertuples(index=False):
    MILES[row.from_id, row.to_id] = float(row.distance_miles)
    DRIVE[row.from_id, row.to_id] = float(row.drive_hours)


def miles(a, b):
    return 0.0 if a == b else MILES[a, b]          # same place = 0 miles (no row for it in the sheet)


def drive_hours(a, b):
    return 0.0 if a == b else DRIVE[a, b]


# STEP 2 - quick checks on the data (data README section 6)

problems = []
if shipments_df["shipment_id"].duplicated().any():
    problems.append("duplicate shipment IDs")
locations = set(FAC) | set(DEPOT)
for i in SHIPMENTS:
    s = SHIP[i]
    if s["origin_id"] not in FAC or s["destination_id"] not in FAC:
        problems.append(f"{i}: unknown facility")
    if not (s["pickup_open_hour"] < s["pickup_close_hour"]
            and s["delivery_open_hour"] < s["delivery_close_hour"]):
        problems.append(f"{i}: window not ordered")
for k in TRACTORS:
    if TRACTOR[k]["home_depot_id"] not in DEPOT or TRACTOR[k]["equipment_id"] not in EQUIP:
        problems.append(f"{k}: unknown depot or equipment")
for a in locations:
    for b in locations:
        if a != b and (a, b) not in MILES:
            problems.append(f"missing travel row {a}->{b}")
if problems:
    raise ValueError("Data problems:\n  " + "\n  ".join(problems))


# STEP 3 - parameters
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
    return float(m.iloc[0]["base_cost_cad"]) * (1 + float(m.iloc[0]["fuel_surcharge_pct"]))


ltl_cost = {i: ltl_cost_of(i) for i in SHIPMENTS}

# each shipment has 2 stops: "P_S001" is the pickup of S001 and "D_S001" is the delivery
kind, ship_of, location, service_hours = {}, {}, {}, {}
earliest_start, latest_finish = {}, {}
for i in SHIPMENTS:
    s = SHIP[i]
    d = int(s["day"])
    for u, kd, loc, svc, op, cl in [
            (f"P_{i}", "P", s["origin_id"], s["pickup_service_hours"],
             s["pickup_open_hour"], s["pickup_close_hour"]),
            (f"D_{i}", "D", s["destination_id"], s["delivery_service_hours"],
             s["delivery_open_hour"], s["delivery_close_hour"])]:
        kind[u], ship_of[u], location[u], service_hours[u] = kd, i, loc, float(svc)
        # service has to fit both the shipment window and the facility hours (time = 24*day + hour)
        earliest_start[u] = 24 * d + max(op, FAC[loc]["open_hour"])
        latest_finish[u] = 24 * d + min(cl, FAC[loc]["close_hour"])


def tour_bounds(k, d):
    """Earliest start and latest end of a tour: it has to fit in the shift, the depot hours and the horizon."""
    t = TRACTOR[k]
    h = DEPOT[t["home_depot_id"]]
    start = 24 * d + max(t["shift_start_hour"], h["open_hour"])
    end = min(24 * d + min(t["shift_end_hour"], h["close_hour"]), 24 * horizon_end_day)
    return start, end


# group the trucks that are exactly the same (same depot, truck type and shift)
groups = {}
for k in TRACTORS:
    t = TRACTOR[k]
    groups.setdefault((t["home_depot_id"], t["equipment_id"],
                       t["shift_start_hour"], t["shift_end_hour"]), []).append(k)



# STEP 4 - build the Gurobi model
def add_day(m, d):
    """Adds all the variables and constraints for one day to the model.
    (a day-d tour can only carry day-d shipments)"""
    I = SHIPMENTS_ON_DAY[d]
    K = TRACTORS
    STOPS = [f"P_{i}" for i in I] + [f"D_{i}" for i in I]
    START, END = "START", "END"

    def loc(u, k):
        return TRACTOR[k]["home_depot_id"] if u in (START, END) else location[u]

    # ---- which legs are allowed ----
    # from the depot (START) the truck can only go to a pickup since it's empty,
    # and it can only go back to the depot (END) after a delivery.
    # we also skip legs that are impossible time-wise (finishing at u and
    # driving to v would already be too late for v)
    LEGS = {}
    for k in K:
        legs = [(START, v) for v in STOPS if kind[v] == "P"]
        for u in STOPS:
            for v in STOPS:
                if u == v or (kind[u] == "D" and v == "P_" + ship_of[u]):
                    continue
                if (earliest_start[u] + service_hours[u] + drive_hours(loc(u, k), loc(v, k))
                        > latest_finish[v] - service_hours[v] + EPS):
                    continue
                legs.append((u, v))
            if kind[u] == "D":
                legs.append((u, END))
        LEGS[k] = legs

    day0, day1 = 24.0 * d, 24.0 * (d + 1)       # every time on day d is between day0 and day1
    M_time = day1 - day0                        # big M for time = 24 hours
    n_max = max_stops // 2

    # ---- Step 1: decision variables ----
    V = {}
    V["use_ltl"] = m.addVars(I, vtype=GRB.BINARY, name=f"use_ltl_d{d}")
    V["on_tour"] = m.addVars(I, K, vtype=GRB.BINARY, name=f"on_tour_d{d}")
    V["tour_used"] = m.addVars(K, vtype=GRB.BINARY, name=f"tour_used_d{d}")
    V["is_consolidated"] = m.addVars(K, vtype=GRB.BINARY, name=f"is_consolidated_d{d}")
    V["drive_leg"] = m.addVars([(u, v, k) for k in K for (u, v) in LEGS[k]],
                               vtype=GRB.BINARY, name=f"drive_leg_d{d}")
    V["depot_departure"] = m.addVars(K, lb=day0, ub=day1, name=f"depot_departure_d{d}")
    V["return_time"] = m.addVars(K, lb=day0, ub=day1, name=f"return_time_d{d}")
    V["arrival"] = m.addVars(STOPS, K, lb=day0, ub=day1, name=f"arrival_d{d}")
    V["service_start"] = m.addVars(STOPS, K, lb=day0, ub=day1, name=f"service_start_d{d}")
    V["stop_departure"] = m.addVars(STOPS, K, lb=day0, ub=day1, name=f"stop_departure_d{d}")
    V["load_weight"] = m.addVars(STOPS, K, name=f"load_weight_d{d}")
    V["load_feet"] = m.addVars(STOPS, K, name=f"load_feet_d{d}")
    V["load_pallets"] = m.addVars(STOPS, K, name=f"load_pallets_d{d}")
    V["linehaul_charge"] = m.addVars(K, name=f"linehaul_charge_d{d}")
    V["stop_charge"] = m.addVars(K, name=f"stop_charge_d{d}")

    use_ltl, on_tour, tour_used = V["use_ltl"], V["on_tour"], V["tour_used"]
    is_consolidated, drive_leg = V["is_consolidated"], V["drive_leg"]
    depot_departure, return_time = V["depot_departure"], V["return_time"]
    arrival, service_start, stop_departure = V["arrival"], V["service_start"], V["stop_departure"]
    loads = [(V["load_weight"], "weight_lb", "weight_capacity_lb"),
             (V["load_feet"], "linear_feet", "length_capacity_feet"),
             (V["load_pallets"], "pallets", "pallet_capacity")]
    linehaul_charge, stop_charge = V["linehaul_charge"], V["stop_charge"]

    out_legs = {(u, k): [] for k in K for u in STOPS + [START]}
    in_legs = {(v, k): [] for k in K for v in STOPS + [END]}
    for k in K:
        for (u, v) in LEGS[k]:
            out_legs[u, k].append(v)
            in_legs[v, k].append(u)

    # ---- Step 3: constraints ----
    n_ship = {k: gp.quicksum(on_tour[i, k] for i in I) for k in K}

    # each shipment is served exactly once (truck or LTL)
    m.addConstrs((on_tour.sum(i, "*") + use_ltl[i] == 1 for i in I), name=f"C1_d{d}")
    # a shipment can only be on a tour that is used
    m.addConstrs((on_tour[i, k] <= tour_used[k] for i in I for k in K), name=f"C3_d{d}")
    # a used tour has to carry at least 1 shipment
    m.addConstrs((tour_used[k] <= n_ship[k] for k in K), name=f"C4_d{d}")
    # consolidated tour = 2 or more shipments
    m.addConstrs((n_ship[k] >= 2 * is_consolidated[k] for k in K), name=f"C5a_d{d}")
    m.addConstrs((n_ship[k] <= tour_used[k] + (n_max - 1) * is_consolidated[k]
                  for k in K), name=f"C5b_d{d}")
    # if the truck is used it leaves its depot once
    m.addConstrs((gp.quicksum(drive_leg[START, v, k] for v in out_legs[START, k])
                  == tour_used[k] for k in K), name=f"C6_d{d}")
    # the truck comes back to the same depot once
    m.addConstrs((gp.quicksum(drive_leg[u, END, k] for u in in_legs[END, k])
                  == tour_used[k] for k in K), name=f"C7_d{d}")
    # flow: the truck goes in and out of each of its stops once (same truck does pickup and delivery)
    m.addConstrs((gp.quicksum(drive_leg[u, v, k] for v in out_legs[u, k])
                  == on_tour[ship_of[u], k] for u in STOPS for k in K), name=f"C8out_d{d}")
    m.addConstrs((gp.quicksum(drive_leg[v, u, k] for v in in_legs[u, k])
                  == on_tour[ship_of[u], k] for u in STOPS for k in K), name=f"C8in_d{d}")
    # only the legs in LEGS[k] exist (we built them above)

    for k in K:
        cap = EQUIP[TRACTOR[k]["equipment_id"]]
        for (u, v) in LEGS[k]:
            x = drive_leg[u, v, k]
            leave_u = depot_departure[k] if u == START else stop_departure[u, k]
            reach_v = return_time[k] if v == END else arrival[v, k]
            t_uv = drive_hours(loc(u, k), loc(v, k))
            # timing: if the truck drives u -> v, arrival at v = departure from u + driving time (big M)
            m.addConstr(reach_v >= leave_u + t_uv - (M_time + t_uv) * (1 - x),
                        name=f"C10a[{u},{v},{k},{d}]")
            m.addConstr(reach_v <= leave_u + t_uv + M_time * (1 - x),
                        name=f"C10b[{u},{v},{k},{d}]")
            # loads: load after a stop = load before +/- that shipment (weight, feet and pallets)
            if v != END:
                sgn = 1 if kind[v] == "P" else -1
                for var, col, capcol in loads:
                    before = 0 if u == START else var[u, k]          # truck starts empty
                    dem = SHIP[ship_of[v]][col]
                    bigM = cap[capcol] + dem
                    m.addConstr(var[v, k] >= before + sgn * dem - bigM * (1 - x),
                                name=f"C26a[{u},{v},{k},{d},{col}]")
                    m.addConstr(var[v, k] <= before + sgn * dem + bigM * (1 - x),
                                name=f"C26b[{u},{v},{k},{d},{col}]")

        for u in STOPS:
            i = ship_of[u]
            # can only start loading after arriving (waiting is ok, it counts as duty)
            m.addConstr(service_start[u, k] >= arrival[u, k], name=f"C11[{u},{k},{d}]")
            # truck leaves after the 0.5 h of handling
            m.addConstr(stop_departure[u, k] >= service_start[u, k] + service_hours[u],
                        name=f"C12[{u},{k},{d}]")
            # start time: not before the window opens / facility opens
            m.addConstr(service_start[u, k] >= earliest_start[u], name=f"C13[{u},{k},{d}]")
            # finish time: has to be done before the window and facility close
            m.addConstr(service_start[u, k] + service_hours[u]
                        <= latest_finish[u] + M_time * (1 - on_tour[i, k]),
                        name=f"C14[{u},{k},{d}]")
            # capacity limits after every stop (45,000 lb / 53 ft / 26 pallets)
            for var, _, capcol in loads:
                var[u, k].UB = cap[capcol]

        for i in I:
            # pickup has to happen before the delivery
            m.addConstr(service_start[f"D_{i}", k] >= service_start[f"P_{i}", k]
                        + service_hours[f"P_{i}"] - M_time * (1 - on_tour[i, k]),
                        name=f"C15[{i},{k},{d}]")

        earliest, latest = tour_bounds(k, d)
        c = TLCOST[TRACTOR[k]["equipment_id"]]
        # leave the depot after the shift starts and the depot opens
        m.addConstr(depot_departure[k] >= earliest, name=f"C16[{k},{d}]")
        # be back before the shift ends, the depot closes and the horizon ends
        m.addConstr(return_time[k] <= latest, name=f"C17_C18[{k},{d}]")
        # max 8 h of driving (depot legs count too)
        m.addConstr(gp.quicksum(drive_hours(loc(u, k), loc(v, k)) * drive_leg[u, v, k]
                                for (u, v) in LEGS[k]) <= max_drive_hours * tour_used[k],
                    name=f"C19[{k},{d}]")
        # max 12 h of duty (return time - departure time), and duty cannot be negative
        m.addConstr(return_time[k] - depot_departure[k] <= max_duty_hours * tour_used[k],
                    name=f"C20[{k},{d}]")
        m.addConstr(return_time[k] >= depot_departure[k], name=f"C20b[{k},{d}]")
        # max 6 stops, so max 3 shipments
        m.addConstr(2 * n_ship[k] <= max_stops * tour_used[k], name=f"C29[{k},{d}]")
        # linehaul = max(minimum charge, miles x rate), counting the depot legs too
        m.addConstr(linehaul_charge[k] >= c["minimum_linehaul_cad"] * tour_used[k],
                    name=f"C30a[{k},{d}]")
        m.addConstr(linehaul_charge[k] >= c["linehaul_cad_per_mile"]
                    * gp.quicksum(miles(loc(u, k), loc(v, k)) * drive_leg[u, v, k]
                                  for (u, v) in LEGS[k]), name=f"C30b[{k},{d}]")
        # stop fee = max(0, stops - 2) x 50
        m.addConstr(stop_charge[k] >= c["extra_stop_cad"]
                    * (2 * n_ship[k] - 2 * tour_used[k]), name=f"C31[{k},{d}]")

    # speed-up: trucks at the same depot are identical, so swapping their tours
    # gives the same plan. we force them to be used in order (by a "key") so
    # Gurobi doesn't waste time on those duplicate solutions.
    if USE_SYMMETRY_BREAKING:
        weight = {i: n + 1 for n, i in enumerate(I)}
        for members in groups.values():
            for k1, k2 in zip(members, members[1:]):
                m.addConstr(tour_used[k1] >= tour_used[k2], name=f"SB_used[{k1},{k2},{d}]")
                m.addConstr(gp.quicksum(weight[i] * on_tour[i, k1] for i in I)
                            >= gp.quicksum(weight[i] * on_tour[i, k2] for i in I),
                            name=f"SB_key[{k1},{k2},{d}]")

    # ---- Step 2: objective (cost for this day) ----
    day_cost = (gp.quicksum(linehaul_charge[k] + stop_charge[k] for k in K)
                + gp.quicksum(ltl_cost[i] * use_ltl[i] for i in I))
    V["LEGS"], V["STOPS"], V["loc"] = LEGS, STOPS, loc
    return V, day_cost


def build_model(days):
    model = gp.Model("Consolidation_Full_Gurobi")
    parts, cost = {}, 0
    for d in days:
        parts[d], day_cost = add_day(model, d)
        cost += day_cost
    # Step 2: objective = linehaul + stop fees + LTL
    model.setObjective(cost, GRB.MINIMIZE)
    # rest between days (only needed if all days are in one model)
    for d in days:
        if d + 1 in days:
            a, b = parts[d], parts[d + 1]
            for k in TRACTORS:
                model.addConstr(b["depot_departure"][k] - a["return_time"][k]
                                >= min_rest_hours - 48 * (2 - a["tour_used"][k]
                                                          - b["tour_used"][k]),
                                name=f"C21[{k},{d}]")
    model.Params.MIPGap = 0.0
    model.Params.TimeLimit = TIME_LIMIT_SECONDS
    model.Params.Seed = 0
    return model, parts


# STEP 5 - solve
# days don't share any shipments or tours, so solving them one at a time
# gives the same answer. in that case we check the rest rule using the shifts.
if ONE_MODEL_PER_DAY:
    for k in TRACTORS:
        t = TRACTOR[k]
        if 24 - (t["shift_end_hour"] - t["shift_start_hour"]) + EPS < min_rest_hours:
            raise ValueError(f"{k}: shifts do not guarantee {min_rest_hours} h rest (C21)")
    batches = [[d] for d in DAYS]
else:
    batches = [DAYS]

STATUS = {GRB.OPTIMAL: "OPTIMAL", GRB.TIME_LIMIT: "TIME_LIMIT",
          GRB.INFEASIBLE: "INFEASIBLE", GRB.INF_OR_UNBD: "INF_OR_UNBD"}
solved = []                                    # store each solved model
for days in batches:
    print(f"\n######## Solving day(s) {days} ########")
    model, parts = build_model(days)
    model.optimize()
    if model.SolCount == 0:
        raise SystemExit(f"No feasible solution found for day(s) {days} "
                         f"(status {STATUS.get(model.Status, model.Status)})")
    solved.append((model, parts))


# =====================================================================
# STEP 6 - Gurobi report (what the rubric asks for)
# =====================================================================
total_obj = sum(mdl.ObjVal for mdl, _ in solved)
total_bound = sum(mdl.ObjBound for mdl, _ in solved)
print("\n================ GUROBI REPORT ================")
print(f"Gurobi version     : {'.'.join(map(str, gp.gurobi.version()))}")
print(f"Settings           : MIPGap = 0, TimeLimit = {TIME_LIMIT_SECONDS} s per model, "
      f"Seed = 0, symmetry breaking = {USE_SYMMETRY_BREAKING}, "
      f"one model per day = {ONE_MODEL_PER_DAY}")
for (mdl, parts) in solved:
    print(f"Day(s) {list(parts)}: {mdl.NumVars} vars ({mdl.NumBinVars} binary), "
          f"{mdl.NumConstrs} constraints | status {STATUS.get(mdl.Status, mdl.Status)} | "
          f"objective {mdl.ObjVal:,.2f} | bound {mdl.ObjBound:,.2f} | "
          f"gap {mdl.MIPGap:.2%} | {mdl.Runtime:.1f} s")
print(f"Total objective    : CAD {total_obj:,.2f}")
print(f"Total best bound   : CAD {total_bound:,.2f}")
gap = (total_obj - total_bound) / total_obj if total_obj else 0
print(f"Overall gap        : {gap:.4%}")
if all(mdl.Status == GRB.OPTIMAL for mdl, _ in solved):
    print("All models solved to optimality: this is a proven GLOBAL optimum.")
else:
    print("Time limit reached: the plan is feasible, but optimality is NOT proven "
          "(see the gap).")


# STEP 7 - get the plan back from the Gurobi variables
def fmt(h):
    day, rem = divmod(h, 24)
    hh, mm = divmod(round(rem * 60), 60)
    return f"Day {int(day)} {int(hh):02d}:{int(mm):02d}"


tours = []
on_ltl = []
for (mdl, parts) in solved:
    for d, V in parts.items():
        on_ltl += [i for i in SHIPMENTS_ON_DAY[d] if V["use_ltl"][i].X > 0.5]
        for k in TRACTORS:
            if V["tour_used"][k].X < 0.5:
                continue
            nxt = {u: v for (u, v) in V["LEGS"][k] if V["drive_leg"][u, v, k].X > 0.5}
            route, u = [], nxt["START"]
            while u != "END":
                route.append(u)
                u = nxt[u]
            loc = V["loc"]
            stops, prev = [], "START"
            for u in route:
                stops.append({
                    "event": kind[u], "shipment": ship_of[u], "location": location[u],
                    "leg_miles": miles(loc(prev, k), location[u]),
                    "leg_drive": drive_hours(loc(prev, k), location[u]),
                    "arrival": V["arrival"][u, k].X,
                    "service_start": V["service_start"][u, k].X,
                    "service_end": V["service_start"][u, k].X + service_hours[u],
                    "departure": V["stop_departure"][u, k].X,
                    "wait": V["service_start"][u, k].X - V["arrival"][u, k].X,
                    "load_weight": V["load_weight"][u, k].X,
                    "load_feet": V["load_feet"][u, k].X,
                    "load_pallets": V["load_pallets"][u, k].X})
                prev = u
            home = TRACTOR[k]["home_depot_id"]
            tour_miles = sum(miles(loc(a, k), loc(b, k))
                             for a, b in zip(["START"] + route, route + ["END"]))
            tour_drive = sum(drive_hours(loc(a, k), loc(b, k))
                             for a, b in zip(["START"] + route, route + ["END"]))
            tours.append({
                "tractor": k, "day": d, "route": route, "stops": stops,
                "shipments": sorted({ship_of[u] for u in route}),
                "departure": V["depot_departure"][k].X,
                "return_time": V["return_time"][k].X,
                "miles": tour_miles, "drive_hours": tour_drive,
                "duty_hours": V["return_time"][k].X - V["depot_departure"][k].X,
                "return_miles": miles(location[route[-1]], home),
                "return_drive": drive_hours(location[route[-1]], home),
                "linehaul": V["linehaul_charge"][k].X,
                "stop_fee": V["stop_charge"][k].X,
                "cost": V["linehaul_charge"][k].X + V["stop_charge"][k].X})
tours.sort(key=lambda t: (t["day"], t["tractor"]))


# STEP 8 - results
all_ltl = sum(ltl_cost.values())
savings = all_ltl - total_obj
dedicated = [t for t in tours if len(t["shipments"]) == 1]
shared = [t for t in tours if len(t["shipments"]) >= 2]
print("\n================ RESULTS ================")
print(f"All-LTL baseline   : CAD {all_ltl:,.2f}")
print(f"Optimized cost     : CAD {total_obj:,.2f}")
print(f"Savings            : CAD {savings:,.2f}  ({savings / all_ltl:.2%})")
print(f"Shipments on LTL           : {len(on_ltl)}")
print(f"Shipments on dedicated tour: {sum(len(t['shipments']) for t in dedicated)} "
      f"({len(dedicated)} tours)")
print(f"Shipments on shared tour   : {sum(len(t['shipments']) for t in shared)} "
      f"({len(shared)} tours)")
print(f"Total tour miles   : {sum(t['miles'] for t in tours):,.0f}")

print("\n================ SELECTED TOURS ================")
for n, t in enumerate(tours, 1):
    k = t["tractor"]
    print(f"\nTour {n}: {k} ({TRACTOR[k]['driver_name']}), Day {t['day']}, "
          f"shipments {', '.join(t['shipments'])}")
    print(f"  depart {fmt(t['departure'])} -> return {fmt(t['return_time'])} | "
          f"{t['miles']:.0f} mi, drive {t['drive_hours']:.2f} h, "
          f"duty {t['duty_hours']:.2f} h | cost CAD {t['cost']:,.2f}")
    for s in t["stops"]:
        verb = "pick up" if s["event"] == "P" else "deliver"
        print(f"    {fmt(s['arrival'])} arrive {s['location']}, {verb} {s['shipment']} "
              f"{fmt(s['service_start'])[-5:]}-{fmt(s['service_end'])[-5:]} "
              f"(wait {s['wait']:.2f} h) | on board {s['load_weight']:,.0f} lb, "
              f"{s['load_feet']:.0f} ft, {s['load_pallets']:.0f} pal")
print("\nShipments left on LTL:", ", ".join(on_ltl) if on_ltl else "none")


# STEP 9 - save the plan to Excel 
tour_rows, stop_rows = [], []
for n, t in enumerate(tours, 1):
    k = t["tractor"]
    home = TRACTOR[k]["home_depot_id"]
    tour_rows.append({"tour_id": n, "tractor_id": k, "driver_name": TRACTOR[k]["driver_name"],
                      "equipment_id": TRACTOR[k]["equipment_id"], "home_depot_id": home,
                      "day": t["day"], "shipments": ",".join(t["shipments"]),
                      "num_shipments": len(t["shipments"]), "num_events": len(t["route"]),
                      "departure_abs": t["departure"], "return_abs": t["return_time"],
                      "miles": t["miles"], "drive_hours": t["drive_hours"],
                      "duty_hours": t["duty_hours"], "linehaul_cad": t["linehaul"],
                      "stop_fee_cad": t["stop_fee"], "tour_cost_cad": t["cost"]})
    stop_rows.append({"tour_id": n, "tractor_id": k, "day": t["day"], "seq": 0,
                      "event": "DEPART", "shipment_id": "", "location_id": home,
                      "arrival_abs": None, "service_start_abs": None, "service_end_abs": None,
                      "departure_abs": t["departure"], "wait_hours": 0, "leg_miles": 0,
                      "leg_drive_hours": 0, "load_weight_lb": 0, "load_feet": 0,
                      "load_pallets": 0})
    for q, s in enumerate(t["stops"], 1):
        stop_rows.append({"tour_id": n, "tractor_id": k, "day": t["day"], "seq": q,
                          "event": "PICKUP" if s["event"] == "P" else "DELIVERY",
                          "shipment_id": s["shipment"], "location_id": s["location"],
                          "arrival_abs": s["arrival"], "service_start_abs": s["service_start"],
                          "service_end_abs": s["service_end"], "departure_abs": s["departure"],
                          "wait_hours": s["wait"], "leg_miles": s["leg_miles"],
                          "leg_drive_hours": s["leg_drive"],
                          "load_weight_lb": s["load_weight"], "load_feet": s["load_feet"],
                          "load_pallets": s["load_pallets"]})
    stop_rows.append({"tour_id": n, "tractor_id": k, "day": t["day"],
                      "seq": len(t["stops"]) + 1, "event": "RETURN", "shipment_id": "",
                      "location_id": home, "arrival_abs": t["return_time"],
                      "service_start_abs": None, "service_end_abs": None,
                      "departure_abs": None, "wait_hours": 0,
                      "leg_miles": t["return_miles"], "leg_drive_hours": t["return_drive"],
                      "load_weight_lb": 0, "load_feet": 0, "load_pallets": 0})

summary = [("gurobi_version", ".".join(map(str, gp.gurobi.version()))),
           ("status", ", ".join(STATUS.get(m_.Status, str(m_.Status)) for m_, _ in solved)),
           ("runtime_seconds", round(sum(m_.Runtime for m_, _ in solved), 2)),
           ("objective_cad", round(total_obj, 2)), ("best_bound_cad", round(total_bound, 2)),
           ("gap", gap), ("all_ltl_cad", round(all_ltl, 2)),
           ("savings_cad", round(savings, 2)),
           ("savings_pct", round(100 * savings / all_ltl, 2)),
           ("shipments_ltl", len(on_ltl))]
with pd.ExcelWriter(PLAN_FILE) as writer:
    pd.DataFrame(tour_rows).to_excel(writer, sheet_name="tours", index=False)
    pd.DataFrame(stop_rows).to_excel(writer, sheet_name="stops", index=False)
    pd.DataFrame([{"shipment_id": i, "day": int(SHIP[i]["day"]),
                   "ltl_cost_cad": ltl_cost[i]} for i in on_ltl]).to_excel(
        writer, sheet_name="ltl", index=False)
    pd.DataFrame(summary, columns=["item", "value"]).to_excel(
        writer, sheet_name="summary", index=False)
print(f"\nPlan written to {PLAN_FILE.name}")