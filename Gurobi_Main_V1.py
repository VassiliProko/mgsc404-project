# main file
#
# How this program works:
#   1. First we build a list of truck tours that could work (1 to 4
#      shipments per tour, every stop order the loading rule allows, every
#      type of truck). A tour is only kept if it follows all the rules:
#        - pickup and delivery windows + facility hours (they vary here)
#        - depot hours and the driver's shift
#        - max 11 h driving and 14 h duty
#        - a 30 min break before driving more than 8 h since the last break
#        - weight, floor length and pallets after every stop (reefers are smaller)
#        - max 8 stops (so max 4 shipments)
#        - chilled freight requires a reefer; ambient freight can use either truck type
#        - only freight with the same temperature and group on board together
#        - last in, first out: the last shipment loaded has to come off first
#      We also work out what each tour costs.
#   2. Then Gurobi picks the cheapest combination:
#        variables:   select_tour[r] and use_ltl[i] (both binary)
#        objective:   minimize total tour cost + total LTL cost
#        constraints: every shipment is served exactly once (truck or LTL)
#                     and each truck does at most one tour per day
#   3. The results get saved in an Excel file
#
# How we build the candidate tours (the main file is too big to blindly try
# every combination of 4 shipments):
#   - a group of 3 is only tried if every pair inside it already works on that
#     type of truck, and a group of 4 only if every group of 3 inside it works.
#     (we tested this on the main file by trying every group of 3 and 4 without
#     this shortcut and it didn't lose any tour)
#   - for the break we first try the simple rule: take it at the last stop before
#     the 8 h would be passed (in the waiting time if there's at least 30 min of
#     it, otherwise right after loading/unloading). if that doesn't work we try
#     every place the break could go (before or after service at any stop, up to
#     2 breaks) and every departure time, 15 min apart
#   - optional cap (MAX_GROUPS_PER_SIZE) on how many groups we try per size
#     (off by default)
# A 0% gap means optimal over the tours we gave Gurobi. Since the shortcut
# didn't lose anything in our test, this should be all the feasible tours.

import itertools
import time
from pathlib import Path

import pandas as pd
import gurobipy as gp
from gurobipy import GRB

import subprocess
import sys

# settings we can change
FOLDER = Path(__file__).resolve().parent
DATA_FILE = FOLDER / "data" / "project1_main.xlsx"
OUTPUT_FOLDER = FOLDER / "output"
OUTPUT_FOLDER.mkdir(exist_ok=True)          # creates the folder if it doesn't exist yet
PLAN_FILE = OUTPUT_FOLDER / "plan_main.xlsx"

TIME_LIMIT_SECONDS = 1800         # time limit for Gurobi (seconds)
MAX_SHIPMENTS_PER_TOUR = None     # None = take it from max_stops in the file (8 stops -> 4)
MAX_GROUPS_PER_SIZE = None        # None = no cap; e.g. 50000 to make it run faster
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

# rule values from the file (main file values in the comments)
max_drive_hours = float(rules["max_drive_hours"])            # 11 h
max_duty_hours = float(rules["max_duty_hours"])              # 14 h
min_rest_hours = float(rules["min_rest_hours"])              # 10 h
break_after = float(rules["break_after_drive_hours"])        # break before passing 8 h of driving
break_length = float(rules["break_hours"])                   # 0.5 h break
max_stops = int(params["max_stops"])                         # 8 stops = max 4 shipments
lifo_required = int(params["lifo_required"])                 # 1 = last in, first out
demand_days = int(params["demand_days"])                     # days 1-7
horizon_end_day = int(params["horizon_end_day"])             # ends at 00:00 on day 8
time_step = float(params["time_step_hours"])                 # 15 minute steps

if MAX_SHIPMENTS_PER_TOUR is None:
    MAX_SHIPMENTS_PER_TOUR = max_stops // 2

# sets
SHIPMENTS = list(shipments_df["shipment_id"])
TRACTORS = list(tractors_df["tractor_id"])
DAYS = list(range(1, demand_days + 1))
SHIPMENTS_ON_DAY = {d: [i for i in SHIPMENTS if int(SHIP[i]["day"]) == d]
                    for d in DAYS}

# freight type of each shipment: only the same (temperature, group) can share the truck
temperature = {i: str(SHIP[i]["temperature"]).strip().lower() for i in SHIPMENTS}
freight_type = {i: (temperature[i], str(SHIP[i]["compatibility_group"]).strip().lower())
                for i in SHIPMENTS}
is_chilled = {i: temperature[i] == "chilled" for i in SHIPMENTS}

# distances and driving times (each direction separately)
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
        if temperature[i] not in ("ambient", "chilled"):
            problems.append(f"{i}: unknown temperature '{temperature[i]}'")
    for f, row in FAC.items():
        if not row["open_hour"] < row["close_hour"]:
            problems.append(f"{f}: facility hours not ordered")
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
            elif a != b and DRIVE[a, b] > break_after + EPS:
                problems.append(f"lane {a}->{b} is longer than {break_after} h, "
                                f"a break would be needed while driving")
    if problems:
        raise ValueError("Data problems:\n  " + "\n  ".join(problems))


check_data()


# Step 3: parameters

# LTL cost of each shipment (has to match exactly one rate row)
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
        raise ValueError(f"{k}: shifts give only {rest} h rest between days")


# Step 4: build the candidate tours
def schedule_tour(k, d, sequence, depart, check_duty=True, breaks=None):
    """Simulates one tour stop by stop, for a given stop order and departure time.
    Returns the whole schedule, or None if any rule is broken.
    check_duty=False is only used on the first try (leaving as early as
    possible), because we can still cut the duty later by leaving later.
    breaks=None uses the simple break rule. Otherwise breaks is a dict like
    {stop number: "before" or "after"} saying exactly where the breaks go."""
    t = TRACTOR[k]
    home = t["home_depot_id"]
    cap = EQUIP[t["equipment_id"]]
    chilled_ok = int(cap["chilled_capable"]) == 1
    _, latest_end = tour_bounds(k, d)
    n = len(sequence)

    now, here = depart, home
    total_drive = total_miles = 0.0
    since_break = 0.0                                    # driving since the last break
    load_w = load_f = load_p = 0.0                       # truck starts empty
    on_board = []                                        # like a stack: last loaded is at the end
    stops = []
    for idx, (kind, i) in enumerate(sequence):
        u = (kind, i)
        s = SHIP[i]
        leg_h, leg_mi = drive_hours(here, location[u]), miles(here, location[u])
        arrival = now + leg_h                            # arrival = left the last stop + driving time
        since_break += leg_h
        if since_break > break_after + EPS:              # would need a break in the middle of driving
            return None
        start = max(arrival, earliest_start[u])          # can't start before the window/facility opens (waiting is ok)
        brk_type, brk_start = "", None
        if breaks is not None and breaks.get(idx) == "before":
            # break when we get there, before loading/unloading starts
            brk_type = "while waiting" if start - arrival >= break_length - EPS else "before service"
            brk_start = arrival
            start = max(start, arrival + break_length)
            since_break = 0.0
        finish = start + service_hours[u]
        if finish > latest_finish[u] + EPS:              # has to finish before the window/facility closes
            return None

        if kind == "P":
            if is_chilled[i] and not chilled_ok:         # chilled freight needs a reefer
                return None
            if on_board and freight_type[on_board[0]] != freight_type[i]:
                return None                              # different temperature or group can't be on board together
            on_board.append(i)
            sign = 1
        else:
            if i not in on_board:
                return None
            if lifo_required and on_board[-1] != i:      # last in, first out
                return None
            on_board.remove(i)
            sign = -1
        load_w += sign * s["weight_lb"]                  # +load at a pickup, -load at a delivery
        load_f += sign * s["linear_feet"]
        load_p += sign * s["pallets"]
        if (load_w > cap["weight_capacity_lb"] + EPS     # check weight, feet and pallets after every stop
                or load_f > cap["length_capacity_feet"] + EPS
                or load_p > cap["pallet_capacity"] + EPS):
            return None

        # break: if the next leg would take us past 8 h since the last break,
        # the driver takes the 30 min break here. if there was at least 30 min
        # of waiting before loading we use that time, otherwise it goes after
        next_loc = location[sequence[idx + 1]] if idx + 1 < n else home
        departure = finish
        if breaks is None:
            if since_break + drive_hours(location[u], next_loc) > break_after + EPS:
                if start - arrival >= break_length - EPS:
                    brk_type, brk_start = "while waiting", arrival
                else:
                    brk_type, brk_start = "after service", finish
                    departure = finish + break_length
                since_break = 0.0
        elif breaks.get(idx) == "after":
            brk_type, brk_start = "after service", finish
            departure = finish + break_length
            since_break = 0.0

        total_drive += leg_h
        total_miles += leg_mi
        stops.append({"event": kind, "shipment": i, "location": location[u],
                      "leg_miles": leg_mi, "leg_drive": leg_h,
                      "arrival": arrival, "service_start": start,
                      "service_end": finish, "wait": start - arrival,
                      "departure": departure,
                      "break_type": brk_type, "break_start": brk_start,
                      "load_weight": load_w, "load_feet": load_f,
                      "load_pallets": load_p})
        now, here = departure, location[u]

    back_h, back_mi = drive_hours(here, home), miles(here, home)
    since_break += back_h
    total_drive += back_h
    total_miles += back_mi
    return_time = now + back_h                           # drive back to the depot
    if since_break > break_after + EPS:                  # the drive home counts for the break rule too
        return None
    if return_time > latest_end + EPS:                   # back before the shift ends / depot closes
        return None
    if total_drive > max_drive_hours + EPS:              # max 11 h of driving
        return None
    if check_duty and return_time - depart > max_duty_hours + EPS:   # max 14 h of duty
        return None
    if on_board:                                         # truck should be empty when it gets back
        return None

    c = TLCOST[t["equipment_id"]]
    n_events = len(sequence)
    linehaul = max(c["minimum_linehaul_cad"],                     # linehaul = max(minimum, miles x rate)
                   c["linehaul_cad_per_mile"] * total_miles)
    stop_fee = max(0, n_events - 2) * c["extra_stop_cad"]         # fee for every stop after the first two
    return {"tractor": k, "day": d, "sequence": tuple(sequence),
            "shipments": tuple(sorted({i for _, i in sequence})),
            "departure": depart, "return_time": return_time,
            "miles": total_miles, "drive_hours": total_drive,
            "duty_hours": return_time - depart, "return_miles": back_mi,
            "return_drive": back_h, "linehaul": linehaul,
            "stop_fee": stop_fee, "cost": linehaul + stop_fee,
            "stops": stops}


def best_departure(k, d, sequence):
    """Picks a departure time that works, trying the one with the least duty first.
    Leaving as early as possible gives the earliest service times, and
    leaving later only cuts waiting time. We first try leaving later by
    min(total waiting, slack); if that breaks something (for example the
    break no longer fits in the waiting time) we try a bit earlier, 15 min
    at a time. The cost doesn't depend on the departure time."""
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
    steps = int(round(delay / time_step)) if time_step > 0 else 0
    for n in range(steps, -1, -1):                       # latest departure first, then earlier
        sch = schedule_tour(k, d, sequence, earliest + n * time_step, check_duty=True)
        if sch is not None:
            return sch
    return None


def search_breaks(k, d, sequence):
    """Backup when the simple break rule doesn't work: try every place the break
    could go (before or after service at any stop, 1 or 2 breaks) and every
    departure time, and keep the schedule with the least duty.
    2 breaks is always enough: with at most 11 h of driving, if there were 3 or
    more breaks we could always drop one and still never pass 8 h in a row."""
    earliest, latest_end = tour_bounds(k, d)
    n = len(sequence)
    spots = [(i, w) for i in range(n) for w in ("before", "after")]
    options = [{i: w} for i, w in spots]
    options += [{i: w, j: v} for (i, w), (j, v) in itertools.combinations(spots, 2) if i != j]
    best = None
    t = earliest
    while t <= latest_end + EPS:
        for brk in options:
            sch = schedule_tour(k, d, sequence, t, check_duty=True, breaks=brk)
            if sch is not None and (best is None or sch["duty_hours"] < best["duty_hours"]):
                best = sch
        t += time_step
    return best


def stop_orders(k, d, group):
    """All stop orders for a group of shipments that pass the quick checks.
    Each pickup comes before its delivery, and with LIFO only the shipment
    on top (the last one loaded) can be delivered. While building the order
    we already throw it away if a window is missed, the truck is overloaded,
    freight types get mixed or a chilled shipment is on a dry truck (we use
    the earliest departure and no break here, so nothing that works is lost)."""
    t = TRACTOR[k]
    home = t["home_depot_id"]
    cap = EQUIP[t["equipment_id"]]
    chilled_ok = int(cap["chilled_capable"]) == 1
    earliest, latest_end = tour_bounds(k, d)
    if any(is_chilled[i] and not chilled_ok for i in group):
        return []
    orders = []

    def extend(seq, now, here, drive, on_board, picked, w, f, p):
        if len(seq) == 2 * len(group):
            if (now + drive_hours(here, home) <= latest_end + EPS
                    and drive + drive_hours(here, home) <= max_drive_hours + EPS):
                orders.append(list(seq))
            return
        options = []
        for i in group:
            if i not in picked:
                if on_board and freight_type[on_board[0]] != freight_type[i]:
                    continue
                options.append(("P", i))
        if on_board:
            if lifo_required:
                options.append(("D", on_board[-1]))
            else:
                options += [("D", i) for i in on_board]
        for u in options:
            kind, i = u
            leg = drive_hours(here, location[u])
            start = max(now + leg, earliest_start[u])
            finish = start + service_hours[u]
            if finish > latest_finish[u] + EPS or drive + leg > max_drive_hours + EPS:
                continue
            s = SHIP[i]
            sign = 1 if kind == "P" else -1
            nw, nf, np_ = w + sign * s["weight_lb"], f + sign * s["linear_feet"], p + sign * s["pallets"]
            if (nw > cap["weight_capacity_lb"] + EPS or nf > cap["length_capacity_feet"] + EPS
                    or np_ > cap["pallet_capacity"] + EPS):
                continue
            nb = on_board + [i] if kind == "P" else [x for x in on_board if x != i]
            extend(seq + [u], finish, location[u], drive + leg, nb,
                   picked | {i} if kind == "P" else picked, nw, nf, np_)

    extend([], earliest, home, 0.0, [], frozenset(), 0.0, 0.0, 0.0)
    return orders


def route_drive(k, sequence):
    """Total driving hours of a stop order (depot -> stops -> depot)."""
    home = TRACTOR[k]["home_depot_id"]
    places = [home] + [location[u] for u in sequence] + [home]
    return sum(drive_hours(a, b) for a, b in zip(places, places[1:]))


def best_tour_for_group(k, d, group):
    """Cheapest stop order that works for this group (ties: less duty)."""
    best = None
    for seq in stop_orders(k, d, group):
        sch = best_departure(k, d, seq)
        if sch is None and route_drive(k, seq) > break_after + EPS:
            sch = search_breaks(k, d, seq)               # maybe the break just needs to go somewhere else
        if sch is None:
            continue
        if (best is None or sch["cost"] < best["cost"] - EPS
                or (abs(sch["cost"] - best["cost"]) <= EPS
                    and sch["duty_hours"] < best["duty_hours"])):
            best = sch
    return best


def next_size_groups(works, size):
    """Groups of `size` shipments where every smaller group inside already works.
    (same idea as building bigger sets only from smaller sets that passed)"""
    by_prefix = {}
    for g in works:
        by_prefix.setdefault(g[:-1], []).append(g[-1])
    groups = []
    for prefix, lasts in by_prefix.items():
        lasts.sort()
        for a, b in itertools.combinations(lasts, 2):
            g = prefix + (a, b)
            if all(sub in works for sub in itertools.combinations(g, size - 1)):
                groups.append(g)
    return groups


def generate_tours():
    """For every day and every type of truck, find the groups of shipments that
    work together and keep the cheapest stop order for each.
    Trucks with the same depot, equipment and shift are identical, so we
    only check one of them and copy the tour to the others."""
    profiles = {}
    for k in TRACTORS:
        t = TRACTOR[k]
        key = (t["home_depot_id"], t["equipment_id"],
               t["shift_start_hour"], t["shift_end_hour"])
        profiles.setdefault(key, []).append(k)

    tours = []
    stats = {size: [0, 0, 0] for size in range(1, MAX_SHIPMENTS_PER_TOUR + 1)}  # tried, worked, capped
    for d in DAYS:                                       # tours only use shipments from the same day
        for key, members in profiles.items():
            k0 = members[0]
            works = set()
            groups = [(i,) for i in sorted(SHIPMENTS_ON_DAY[d])]
            for size in range(1, MAX_SHIPMENTS_PER_TOUR + 1):
                if size > 1:
                    groups = next_size_groups(works, size)
                    works = set()
                if MAX_GROUPS_PER_SIZE and len(groups) > MAX_GROUPS_PER_SIZE:
                    # keep the groups with the most LTL cost to save
                    groups.sort(key=lambda g: -sum(ltl_cost[i] for i in g))
                    stats[size][2] += len(groups) - MAX_GROUPS_PER_SIZE
                    groups = groups[:MAX_GROUPS_PER_SIZE]
                for g in groups:
                    stats[size][0] += 1
                    best = best_tour_for_group(k0, d, g)
                    if best is None:
                        continue
                    stats[size][1] += 1
                    works.add(g)
                    for k in members:                    # copy the tour to every identical truck
                        tours.append(dict(best, tractor=k))
                if not works:
                    break
    return tours, stats, len(profiles)


t0 = time.time()
tours, gen_stats, n_profiles = generate_tours()
generation_seconds = time.time() - t0
print(f"Candidate tours generated: {len(tours)} ({generation_seconds:.1f} s, "
      f"{n_profiles} truck types)")
for size, (tried, worked, capped) in gen_stats.items():
    print(f"  groups of {size}: tried {tried}, feasible {worked}"
          + (f", skipped by cap {capped}" if capped else ""))


# STEP 5 - the Gurobi model that picks the tours
def build_selection_model(tours):
    model = gp.Model("Consolidation_Main")

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


# STEP 6 - Gurobi report and results
STATUS = {GRB.OPTIMAL: "OPTIMAL", GRB.TIME_LIMIT: "TIME_LIMIT",
          GRB.INFEASIBLE: "INFEASIBLE", GRB.INF_OR_UNBD: "INF_OR_UNBD",
          GRB.INTERRUPTED: "INTERRUPTED"}

print("\n================ GUROBI REPORT ================")
print(f"Gurobi version     : {'.'.join(map(str, gp.gurobi.version()))}")
print(f"Settings           : MIPGap = {model.Params.MIPGap}, "
      f"TimeLimit = {model.Params.TimeLimit} s, Seed = {model.Params.Seed}")
print(f"Candidate tours    : {len(tours)} (see the notes at the top on how we built them)")
print(f"Tour generation    : {generation_seconds:.1f} s")
print(f"Gurobi runtime     : {model.Runtime:.2f} s")
print(f"Termination status : {STATUS.get(model.Status, model.Status)}")

if model.SolCount == 0:
    raise SystemExit("No solution found.")

print(f"Objective (CAD)    : {model.ObjVal:,.2f}")
print(f"Best bound (CAD)   : {model.ObjBound:,.2f}")
print(f"Optimality gap     : {model.MIPGap:.4%}")
if model.Status == GRB.OPTIMAL:
    print("Optimal over the candidate tours we generated. (we tested the shortcuts "
          "separately and they didn't lose any feasible tour)")

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
for size in range(1, MAX_SHIPMENTS_PER_TOUR + 1):
    print(f"  tours with {size} shipment(s): {sum(len(t['shipments']) == size for t in chosen)}")
print(f"Tours on reefers           : "
      f"{sum(int(EQUIP[TRACTOR[t['tractor']]['equipment_id']]['chilled_capable']) == 1 for t in chosen)}")
print(f"Chilled shipments on trucks: "
      f"{sum(is_chilled[i] for t in chosen for i in t['shipments'])} of {sum(is_chilled.values())}")
print(f"Tours with a driver break  : {sum(any(s['break_type'] for s in t['stops']) for t in chosen)}")

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
    end = {(s["event"], s["shipment"]): s["service_end"] for s in t["stops"]}
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
    print(f"\nTour {n}: {k} ({TRACTOR[k]['driver_name']}, {TRACTOR[k]['equipment_id']}), "
          f"Day {t['day']}, depot {TRACTOR[k]['home_depot_id']}, "
          f"shipments {', '.join(t['shipments'])}")
    print(f"  depart {fmt(t['departure'])}  ->  return {fmt(t['return_time'])} | "
          f"{t['miles']:.0f} mi, drive {t['drive_hours']:.2f} h, "
          f"duty {t['duty_hours']:.2f} h | cost CAD {t['cost']:,.2f}")
    for s in t["stops"]:
        verb = "pick up" if s["event"] == "P" else "deliver"
        brk = f" + 30 min break {s['break_type']}" if s["break_type"] else ""
        print(f"    {fmt(s['arrival'])} arrive {s['location']}, "
              f"{verb} {s['shipment']} {fmt(s['service_start'])[-5:]}-"
              f"{fmt(s['service_end'])[-5:]} (wait {s['wait']:.2f} h){brk} | on board "
              f"{s['load_weight']:,.0f} lb, {s['load_feet']:.0f} ft, "
              f"{s['load_pallets']:.0f} pal")

print("\nShipments left on LTL:")
for i in on_ltl:
    print(f"  {i}: CAD {ltl_cost[i]:,.2f}")


# STEP 7 - save the plan to Excel (our checker will read this file)
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
    seq = 0
    stop_rows.append({"tour_id": n, "tractor_id": k, "day": t["day"], "seq": seq,
                      "event": "DEPART", "shipment_id": "",
                      "location_id": TRACTOR[k]["home_depot_id"],
                      "arrival_abs": None, "service_start_abs": None,
                      "service_end_abs": None, "departure_abs": t["departure"],
                      "wait_hours": 0, "leg_miles": 0, "leg_drive_hours": 0,
                      "load_weight_lb": 0, "load_feet": 0, "load_pallets": 0})
    for s in t["stops"]:
        seq += 1
        stop_rows.append({"tour_id": n, "tractor_id": k, "day": t["day"], "seq": seq,
                          "event": "PICKUP" if s["event"] == "P" else "DELIVERY",
                          "shipment_id": s["shipment"], "location_id": s["location"],
                          "arrival_abs": s["arrival"],
                          "service_start_abs": s["service_start"],
                          "service_end_abs": s["service_end"],
                          "departure_abs": s["departure"], "wait_hours": s["wait"],
                          "leg_miles": s["leg_miles"], "leg_drive_hours": s["leg_drive"],
                          "load_weight_lb": s["load_weight"], "load_feet": s["load_feet"],
                          "load_pallets": s["load_pallets"]})
        if s["break_type"]:                              # the break gets its own row
            seq += 1
            stop_rows.append({"tour_id": n, "tractor_id": k, "day": t["day"], "seq": seq,
                              "event": "BREAK", "shipment_id": "", "note": s["break_type"],
                              "location_id": s["location"],
                              "arrival_abs": None, "service_start_abs": s["break_start"],
                              "service_end_abs": s["break_start"] + break_length,
                              "departure_abs": None, "wait_hours": 0,
                              "leg_miles": 0, "leg_drive_hours": 0,
                              "load_weight_lb": s["load_weight"], "load_feet": s["load_feet"],
                              "load_pallets": s["load_pallets"]})
    stop_rows.append({"tour_id": n, "tractor_id": k, "day": t["day"],
                      "seq": seq + 1, "event": "RETURN",
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
    ("max_shipments_per_tour", MAX_SHIPMENTS_PER_TOUR),
    ("max_groups_per_size", MAX_GROUPS_PER_SIZE if MAX_GROUPS_PER_SIZE else "no cap"),
    ("objective_cad", round(model.ObjVal, 2)),
    ("best_bound_cad", round(model.ObjBound, 2)),
    ("mip_gap", model.MIPGap),
    ("all_ltl_cad", round(all_ltl, 2)),
    ("savings_cad", round(savings, 2)),
    ("savings_pct", round(100 * savings / all_ltl, 2)),
    ("shipments_ltl", len(on_ltl)),
    ("shipments_dedicated", n_ded),
    ("shipments_consolidated", n_con),
    ("total_miles", total_miles),
]
for size, (tried, worked, capped) in gen_stats.items():
    summary_rows.append((f"groups_of_{size}_tried", tried))
    summary_rows.append((f"groups_of_{size}_feasible", worked))
with pd.ExcelWriter(PLAN_FILE) as writer:
    pd.DataFrame(tour_rows).to_excel(writer, sheet_name="tours", index=False)
    pd.DataFrame(stop_rows).to_excel(writer, sheet_name="stops", index=False)
    pd.DataFrame(ltl_rows).to_excel(writer, sheet_name="ltl", index=False)
    pd.DataFrame(summary_rows, columns=["item", "value"]).to_excel(
        writer, sheet_name="summary", index=False)
print(f"\nPlan written to {PLAN_FILE.relative_to(FOLDER)}")


# Step 8: independently check the saved plan
# The checker rereads the original data and exported plan.
# If any check fails, check=True stops this program with an error.
print("\n================ INDEPENDENT CHECKER ================", flush=True)
subprocess.run(
    [
        sys.executable,
        str(FOLDER / "checker-full.py"),
        "--data", str(DATA_FILE),
        "--plan", str(PLAN_FILE),
    ],
    check=True,
)