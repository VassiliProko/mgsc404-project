# Project 1 – Consolidating Partial Loads into Full Truckloads
## Mixed-Integer Formulation: Decision Variables, Objective, Constraints

MGSC 404 · Formulation reference for the Gurobi implementation (simplified and main workbooks)

**Source abbreviations used throughout**

| Tag | File | Pages |
|---|---|---|
| **[B]** | `Project201-Consolidation.pdf` – project brief | 5 |
| **[R]** | `Readme_project1.pdf` – data README | 6 |

A reference such as **[R p.3, *tractors and depots*]** means: data README, page 3, sub-section "tractors and depots".

The formulation follows the class three-step method (Step 1 decision variables → Step 2 objective → Step 3 constraints, then the compact form with sets). Two equivalent formulations are given:

* **Formulation A – compact MILP (Sections 1–5).** Every rule from the brief and the data README is written as an explicit linear constraint. This is the formulation the rubric grades ([B p.4–5, *Grading – Mixed-integer formulation*]).
* **Formulation B – tour-selection / set-partitioning MILP (Section 6).** This is what Gurobi solves. Feasible tours of Formulation A are generated as candidate columns, and Gurobi selects the plan ([B p.4, *Deliverables 2*]: "A heuristic may propose candidate tours, but it does not replace the Gurobi model that selects the plan").

### Naming convention used in this file

* Variables, parameters and sets have **readable names** written like code: `on_tour[i,k,d]`, `cap_weight[q]`, `TRACTORS`.
* Square brackets hold the indices: `i, j` = shipment, `k` = tractor, `d` = day, `u, v` = stop (node), `q` = equipment type, `f` = facility, `h` = depot, `m` = pair-order configuration.
* SETS are in CAPITALS, parameters (data) in lower case, **decision variables in bold** where they are defined.
* `Σ_{k ∈ TRACTORS}` means "sum over all tractors k". `∀` means "for every".
* §3 includes a glossary mapping each readable name to the short math symbol of the earlier version (V1–V18), so both versions can be cross-referenced.

---

## Contents
1. [Sets and indices](#1-sets-and-indices)
2. [Parameters (data read from the workbook)](#2-parameters)
3. [Step 1 – Decision variables](#3-step-1--decision-variables)
4. [Step 2 – Objective function](#4-step-2--objective-function)
5. [Step 3 – Constraints](#5-step-3--constraints)
6. [Formulation B – tour selection (what Gurobi solves)](#6-formulation-b--tour-selection-model-solved-in-gurobi)
7. [Simplified vs main: which constraints are active](#7-simplified-vs-main-which-constraints-are-active)
8. [Rules deliberately *not* modeled (and why)](#8-rules-deliberately-not-modeled)
9. [Traceability matrix: every rule in the files → model element](#9-traceability-matrix)
10. [Verification log](#10-verification-log)

---

## 1. Sets and indices

| Name | Meaning | Source |
|---|---|---|
| `SHIPMENTS`, index `i, j` | Shipments (one row per **indivisible** shipment) | [R p.1, *shipments*]: "one row per indivisible shipment" |
| `DAYS = {1, …, demand_days}`, index `d` | Demand days (3 simplified, 7 main) | [R p.4, *parameters*]; [B p.3, *Data provided*] |
| `SHIPMENTS_ON_DAY[d]` | Shipments whose stated day is `d` | [R p.1, *shipments*]: "day: the day on which both pickup and delivery must occur" |
| `TRACTORS`, index `k` | Tractor–driver pairs (8 simplified, 24 main) | [R p.3, *tractors and depots*] |
| `FACILITIES`, index `f` | Facilities (12 simplified, 24 main) | [R p.2, *facilities*] |
| `DEPOTS`, index `h` | Depots (2 simplified, 4 main) | [R p.3, *tractors and depots*] |
| `EQUIPMENT_TYPES`, index `q` | Equipment types (DRY; main also REEFER) | [R p.3, *equipment*] |
| `pickup(i)`, `delivery(i)` | Pickup stop and delivery stop of shipment `i` | [B p.2, *Problem statement*]: "Each shipment adds one pickup and one delivery" |
| `PICKUP_STOPS[d] = {pickup(i) : i ∈ SHIPMENTS_ON_DAY[d]}`, `DELIVERY_STOPS[d] = {delivery(i) : i ∈ SHIPMENTS_ON_DAY[d]}`, `STOPS[d] = PICKUP_STOPS[d] ∪ DELIVERY_STOPS[d]` | Service-event stops on day `d` | same |
| `start_depot(k)`, `end_depot(k)` | Start and end copies of tractor `k`'s home depot | [B p.2, *Routing*]: "starting and ending at the tractor's home depot" |
| `NODES[k,d] = STOPS[d] ∪ {start_depot(k), end_depot(k)}` | Nodes of tour `(k,d)` | — |
| `LEGS[k,d]` | Possible driving legs of tour `(k,d)`: `(start_depot(k), v)` for `v ∈ STOPS[d]`; `(u, v)` for `u ≠ v ∈ STOPS[d]`; `(u, end_depot(k))` for `u ∈ STOPS[d]` | [R p.2, *distance_matrix*]: "complete directed travel data" |
| `PAIRS[d] = {(i,j) : i < j, i, j ∈ SHIPMENTS_ON_DAY[d]}` | Unordered shipment pairs on day `d` | needed for loading order and compatibility (main) |
| `INCOMPATIBLE_PAIRS[d] ⊆ PAIRS[d]` | **Incompatible** pairs: different temperature **or** different compatibility group | [R p.2, *shipments*]: "Only shipments with identical groups and identical temperature requirements may be on board together" |

Depot nodes are only tour start and end points. No depot node appears in the middle of a tour ([R p.3]: "Depots are bases, not pickup, delivery or storage locations for freight").

---

## 2. Parameters

All values are read from the workbook, never hard-coded ([R p.6, §6]: "Read the workbook values rather than relying on row positions or example constants").

Units: CAD, **miles**, lb, feet, decimal hours ([R p.1, §2]: "Do not use the kilometre convention from Project 2"; [B p.4]). IDs are model keys. Driver names are reporting labels only ([R p.1, §2]: "IDs are model keys; driver names are reporting labels").

### 2.1 Location and travel
| Name | Meaning | Sheet / column | Source |
|---|---|---|---|
| `origin[i]`, `destination[i]` | Origin and destination facility of `i` | `shipments.origin_id`, `destination_id` | [R p.1] |
| `location(u)` | Location of node `u`: `location(pickup(i)) = origin[i]`, `location(delivery(i)) = destination[i]`, `location(start_depot(k)) = location(end_depot(k)) = home_depot[k]` | — | [R p.1, §2]: "Travel endpoints can be facilities or depots" |
| `miles[u,v]` | Miles from `location(u)` to `location(v)` (**directed**); 0 if same location | `distance_matrix.distance_miles` | [R p.2]: "Same-location travel is zero and has no row. Do not assume a reverse lane has the same value." |
| `drive_hours[u,v]` | Driving hours `location(u) → location(v)`, **stored value** | `distance_matrix.drive_hours` | [B p.4]: "drive_hours is already stored in the sheet"; [R p.2] |

### 2.2 Shipments
| Name | Meaning | Column | Source |
|---|---|---|---|
| `day[i]` | Stated day of `i` | `day` | [R p.1] |
| `pickup_open[i]`, `pickup_close[i]` | Pickup window: earliest service **start**, latest service **completion** | `pickup_open_hour`, `pickup_close_hour` | [R p.1]: "earliest pickup service start and latest pickup service completion. The full handling duration must fit." |
| `delivery_open[i]`, `delivery_close[i]` | Delivery window (same meaning) | `delivery_open_hour`, `delivery_close_hour` | [R p.1] |
| `weight[i]`, `feet[i]`, `pallets[i]` | Weight (lb), floor length (ft), pallets | `weight_lb`, `linear_feet`, `pallets` | [B p.1, *Why it is hard*]; [R p.2]: "three separate resource requirements" |
| `service_hours[pickup(i)]`, `service_hours[delivery(i)]` | Handling time at pickup and at delivery (0.5 h) | `pickup_service_hours`, `delivery_service_hours` | [R p.2]: "continuous handling time at each event" |
| `is_chilled[i] ∈ {0,1}` | 1 if chilled | `temperature` | [R p.2]; [B p.2] |
| `compat_group[i]` | Compatibility group (general / food / industrial) | `compatibility_group` | [R p.2] |
| `freight_class[i]` | Freight class (LTL pricing only) | `freight_class` | [R p.2]: "it does not replace physical capacity checks" |

### 2.3 Facilities, depots, tractors, equipment
| Name | Meaning | Column | Source |
|---|---|---|---|
| `facility_open[f]`, `facility_close[f]` | Facility daily open and close | `facilities.open_hour`, `close_hour` | [R p.2, *facilities*] |
| `depot_open[h]`, `depot_close[h]` | Depot daily open and close | `depots.open_hour`, `close_hour` | [R p.3] |
| `home_depot[k]` | Home depot of `k` | `tractors.home_depot_id` | [R p.3] |
| `equip[k]` | Equipment type of `k` (fixed) | `tractors.equipment_id` | [R p.3]: "The driver–tractor–equipment assignment is fixed" |
| `shift_start[k]`, `shift_end[k]` | Daily shift start and end of `k` | `shift_start_hour`, `shift_end_hour` | [R p.3] |
| `cap_weight[q]`, `cap_feet[q]`, `cap_pallets[q]` | Weight, length and pallet capacity of type `q` | `equipment.*_capacity_*` | [R p.3, *equipment*] |
| `chilled_capable[q] ∈ {0,1}` | 1 if type `q` is chilled-capable | `equipment.chilled_capable` | [R p.3]; [B p.2] |
| `rate_per_mile[q]`, `min_linehaul[q]`, `extra_stop_fee[q]` | Linehaul CAD/mile, minimum linehaul, extra-stop fee | `tl_costs` | [R p.3, *tl_costs*] |

### 2.4 Driver rules and instance parameters
| Name | Meaning (simplified / main) | Source |
|---|---|---|
| `max_drive_hours` | `max_drive_hours` (8 / 11) | [R p.3]; [B p.2 table] |
| `max_duty_hours` | `max_duty_hours` (12 / 14) | [R p.3]; [B p.2 table] |
| `break_after_drive` | `break_after_drive_hours` (8, main) | [R p.3] |
| `break_length` | `break_hours` (0.5, main) | [R p.3] |
| `min_rest_hours` | `min_rest_hours` (12 / 10) | [R p.4] |
| `max_stops` | `max_stops` (6 / 8 service events) | [R p.4]; [B p.2] |
| `lifo_required ∈ {0,1}` | `lifo_required` (0 / 1) | [R p.4]; [B p.2] |
| `horizon_end_day` | `horizon_end_day` (4 / 8; exclusive at hour 0) | [R p.4]; [B p.3] |

### 2.5 Derived parameters (pre-computed in Python)
| Name | Formula | Source |
|---|---|---|
| Absolute time | `24 · day + hour` | [B p.4]; [R p.1, §2] |
| Earliest service start at stop `u`: `earliest_start[u]` | `u = pickup(i)`: `24·day[i] + max(pickup_open[i], facility_open[origin[i]])` · `u = delivery(i)`: `24·day[i] + max(delivery_open[i], facility_open[destination[i]])` | [R p.2]: "Service must satisfy the intersection of facility hours and the shipment's window; neither replaces the other." |
| Latest service completion at `u`: `latest_finish[u]` | `u = pickup(i)`: `24·day[i] + min(pickup_close[i], facility_close[origin[i]])` · `u = delivery(i)`: `24·day[i] + min(delivery_close[i], facility_close[destination[i]])` | same; [R p.2]: "Service may end exactly at closing" (so `≤`) |
| Tour start and end bounds | `tour_earliest_start[k,d] = 24·d + max(shift_start[k], depot_open[home_depot[k]])`, `tour_latest_end[k,d] = 24·d + min(shift_end[k], depot_close[home_depot[k]])` | [R p.3]: "A tour must fit both depot hours and driver shift" |
| Load change at stop `u` | `weight_change[pickup(i)] = +weight[i]`, `weight_change[delivery(i)] = −weight[i]` (same for `feet_change` with `feet[i]` and `pallet_change` with `pallets[i]`) | [R p.2]: "Sum them for the freight on board after each event" |
| LTL cost `ltl_cost[i]` | `ltl_cost[i] = base_cost[i] · (1 + fuel_surcharge[i])`, using the unique band matching class, weight and direct distance `miles(origin[i], destination[i])`; lower bounds inclusive, upper bounds exclusive | [R p.2–3, *ltl_rates*] |
| Big-M values | Time: `M_time[u,v] = latest_finish[u] + drive_hours[u,v] − earliest_start[v]` (or 24). Loads: the capacity. A generic large number is written `M`. | class Big-M method (Module 3) |

---

## 3. Step 1 – Decision variables

*"What decision(s) must be made in order to solve the problem?"* The brief answers this directly: **Assignment, Routing, Timing** ([B p.2, *Problem statement*]). All tour-level variables carry the index pair `[k,d]`, meaning tractor `k`'s tour on day `d`. A shipment `i` can only be on a day-`day[i]` tour.

### 3.0 Glossary

| # | Readable name | One-line meaning |
|---|---|---|
| V1 | `use_ltl[i]` | shipment goes LTL |
| V2 | `on_tour[i,k,d]` |  shipment rides on tractor k's day-d tour |
| V3 | `tour_used[k,d]` |  tractor k drives a tour on day d |
| V4 | `is_consolidated[k,d]` | that tour carries ≥ 2 shipments |
| V5 | `drive_leg[u,v,k,d]` |  truck drives straight from u to v |
| V6 | `depot_departure[k,d]` | time truck leaves the depot |
| V7 | `arrival[u,k,d]`, `return_time[k,d]` | arrival time at a stop; arrival back at the depot |
| V8 | `service_start[u,k,d]` |  loading/unloading starts |
| V9 | `stop_departure[u,k,d]` |  truck leaves the stop |
| V10 | `load_weight[u,k,d]` |  lb on board after the stop |
| V11 | `load_feet[u,k,d]` |  ft on board after the stop |
| V12 | `load_pallets[u,k,d]` |  pallets on board after the stop |
| V13 | `break_while_waiting[u,k,d]` | 0.5 h break during waiting, before service |
| V14 | `break_after_service[u,k,d]` | 0.5 h break after service |
| V15 | `drive_since_break[u,k,d]` |  hours driven since last break, on arrival |
| V16 | `pair_order[i,j,m,k,d]` |  pair (i,j) follows event order m |
| V17 | `linehaul_charge[k,d]` |  linehaul part of the tour cost |
| V18 | `stop_charge[k,d]` | extra-stop part of the tour cost |
| B-V1 | `select_tour[r]` |  candidate tour r is chosen (Formulation B) |
| B-V2 | `use_ltl[i]` | shipment goes LTL (Formulation B) |

### 3.1 Assignment variables

| # | Variable | Type | Meaning | Source in the files |
|---|---|---|---|---|
| **V1** | **`use_ltl[i]`** | binary | 1 if shipment `i` is sent by **LTL** | [B p.4, *Deliverables 1*]: "Binary variables are required where the choice is yes or no, **including whether a shipment uses LTL**"; [B p.2, *Problem statement*]: "choose a dedicated truckload, a shared multi-stop truckload, or LTL" |
| **V2** | **`on_tour[i,k,d]`** | binary | 1 if shipment `i` is carried on tractor `k`'s tour on day `d` | [B p.2, *Assignment*]: "Which shipments share a truck, which move alone on a dedicated truckload"; [R p.1]: "indivisible shipment"; [B p.1]: "each shipment stays on that trailer from its pickup until its delivery" |
| **V3** | **`tour_used[k,d]`** | binary | 1 if tractor `k` performs a tour on day `d` (**tour selected**) | [B p.4, *Deliverables 1*]: "…**and whether a tour is selected**"; [B p.3]: "An unused tractor costs nothing"; [R p.3]: "At most one complete tour is allowed per pair per day" |
| **V4** | **`is_consolidated[k,d]`** | binary | 1 if tour `(k,d)` is **consolidated** (≥ 2 shipments); `tour_used[k,d] − is_consolidated[k,d] = 1` means a **dedicated** tour | [B p.2]: "A dedicated tour carries one shipment… A consolidated tour carries at least two shipments"; [B p.5, *Results*]: "how many shipments are on LTL, on a dedicated tour, and on a tour with at least two shipments" |

### 3.2 Routing variables

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **V5** | **`drive_leg[u,v,k,d]`**, `(u,v) ∈ LEGS[k,d]` | binary | 1 if tractor `k` on day `d` drives directly from node `u` to node `v` | [B p.2, *Routing*]: "The stop sequence for each truckload, starting and ending at the tractor's home depot"; [B p.4, *Deliverables 2*]: "their order" |

### 3.3 Timing variables

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **V6** | **`depot_departure[k,d]`** | continuous | Time tractor `k` **leaves** its depot on day `d` | [B p.2, *Timing*]: "When the tractor leaves the depot" |
| **V7** | **`arrival[u,k,d]`**, `u ∈ STOPS[d]`, and **`return_time[k,d]`** (= arrival at `end_depot(k)`) | continuous | **Arrival** time at node `u`; `return_time[k,d]` is the **return** time | [B p.2, *Timing*]: "when it arrives at each stop, and when it returns" |
| **V8** | **`service_start[u,k,d]`**, `u ∈ STOPS[d]` | continuous | **Service start** time at node `u` | [R p.1]: windows bound "service start" and "service completion"; [R p.2]: "Early trucks may wait outside the site" |
| **V9** | **`stop_departure[u,k,d]`**, `u ∈ STOPS[d]` | continuous | **Departure** time from node `u` (after handling and any post-service break) | needed so a break can be taken "after the last delivery before returning home" [R p.3] |

### 3.4 Load variables (three capacity dimensions)

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **V10** | **`load_weight[u,k,d]`** | continuous ≥ 0 | Weight on board **after** service at `u` | [B p.1]: "Check weight, floor length, and pallets after every stop"; [R p.2]: "Sum them for the freight on board after each event" |
| **V11** | **`load_feet[u,k,d]`** | continuous ≥ 0 | Floor length (ft) on board after `u` | same |
| **V12** | **`load_pallets[u,k,d]`** | continuous ≥ 0 | Pallet positions on board after `u` | same |

### 3.5 Driver-break variables (main file)

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **V13** | **`break_while_waiting[u,k,d]`** | binary | 1 if a 0.5 h break is taken at `u` **while waiting before service** | [R p.3–4]: "waiting can count only if explicitly recorded as uninterrupted off-duty rest" |
| **V14** | **`break_after_service[u,k,d]`** | binary | 1 if a 0.5 h break is taken at `u` **after service** | [R p.3]: "A break can occur at a visited location, including after the last delivery before returning home" |
| **V15** | **`drive_since_break[u,k,d]`** | continuous ≥ 0 | Driving accumulated **since the last qualifying break**, on arrival at `u` | [R p.3]: "break_after_drive_hours: 8 hours of accumulated driving since the last qualifying break" |

### 3.6 Loading-order and compatibility variables (main file)

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **V16** | **`pair_order[i,j,m,k,d]`**, `(i,j) ∈ PAIRS[d]`, `m = 1..6` | binary | 1 if `i` and `j` are both on tour `(k,d)` and their four events occur in **configuration `m`** (table below) | [B p.2]: "strict last-in, first-out"; [R p.2]: "Different groups may use the same tractor at different times after the previous freight has been delivered" |

| `m` | Event order | Name | Allowed when |
|---|---|---|---|
| 1 | `pickup(i) → delivery(i) → pickup(j) → delivery(j)` | `i` entirely before `j` | always |
| 2 | `pickup(j) → delivery(j) → pickup(i) → delivery(i)` | `j` entirely before `i` | always |
| 3 | `pickup(i) → pickup(j) → delivery(j) → delivery(i)` | `j` nested in `i` (LIFO-consistent) | pair compatible |
| 4 | `pickup(j) → pickup(i) → delivery(i) → delivery(j)` | `i` nested in `j` (LIFO-consistent) | pair compatible |
| 5 | `pickup(i) → pickup(j) → delivery(i) → delivery(j)` | crossing (violates LIFO) | compatible **and** side access (`lifo_required = 0`) |
| 6 | `pickup(j) → pickup(i) → delivery(j) → delivery(i)` | crossing (violates LIFO) | compatible **and** side access |

These are the only 6 orders of four events in which each pickup precedes its own delivery (4!/2² = 6). This is the class "choose exactly one mode" construction (Module 3, *Multi-Value Choices*, item 4).

### 3.7 Cost variables

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **V17** | **`linehaul_charge[k,d]`** | continuous ≥ 0 | Linehaul charge of tour `(k,d)` = `max{min_linehaul, rate_per_mile · tour_miles}` (linearized) | [R p.3, *tl_costs*]; [B p.3, *Objective*] |
| **V18** | **`stop_charge[k,d]`** | continuous ≥ 0 | Stop-off charge = `max{0, num_events − 2} · extra_stop_fee` (linearized) | [R p.3, *tl_costs*]; [B p.3]: "stop-off charges" |

### 3.8 Derived quantities (linear expressions, not extra variables)

| Expression | Meaning | Source |
|---|---|---|
| `tour_miles[k,d] = Σ_{(u,v) ∈ LEGS[k,d]} miles[u,v] · drive_leg[u,v,k,d]` | Tour miles, **including empty depot legs** | [R p.3]: "All miles are charged, including empty depot legs"; [B p.3]: "every mile from the depot departure through the return" |
| `num_events[k,d] = Σ_{i ∈ SHIPMENTS_ON_DAY[d]} 2 · on_tour[i,k,d]` | Service events (each pickup and delivery counts, even at a shared address; depot departure and return do not count) | [R p.3, *tl_costs*] |
| `total_drive[k,d] = Σ_{(u,v)} drive_hours[u,v] · drive_leg[u,v,k,d]` | Total driving, including approach and return | [R p.3]: "Include the approach, driving between stops, and the return" |
| `duty[k,d] = return_time[k,d] − depot_departure[k,d]` | Duty = elapsed departure-to-return time | [B p.2]: "Duty is the elapsed time from leaving the depot until returning" |
| `wait[u,k,d] = service_start[u,k,d] − arrival[u,k,d]` | Waiting at `u` (counts as duty) | [R p.2]: "waiting counts as driver duty" |
| `tour_cost[k,d] = linehaul_charge[k,d] + stop_charge[k,d]` | Truckload cost of the tour | [R p.3] |

**Count:** 18 variable families (V1–V18). The brief's required binaries are V1 (`use_ltl`, LTL) and V3 (`tour_used`, tour selected) in Formulation A, and `use_ltl[i]` and `select_tour[r]` in Formulation B.

---

## 4. Step 2 – Objective function

*Minimize total transportation cost* ([B p.2, *Problem statement*]: "so that the total transportation cost is as small as possible").

```
minimize   Σ_{k ∈ TRACTORS} Σ_{d ∈ DAYS} linehaul_charge[k,d]      ← linehaul of TL tours
         + Σ_{k ∈ TRACTORS} Σ_{d ∈ DAYS} stop_charge[k,d]          ← stop-off charges
         + Σ_{i ∈ SHIPMENTS} ltl_cost[i] · use_ltl[i]               ← LTL cost of shipments left on LTL
```

| Term | Source |
|---|---|
| Three-part structure | [B p.3, *Objective*]: "total cost = Σ linehaul cost of truckload tours + Σ stop-off charges + Σ LTL cost of shipments left on LTL" |
| `linehaul_charge`, `stop_charge` use the **assigned tractor's** equipment rates | [B p.3]: "The cost uses the equipment of the assigned tractor" |
| No hourly driver cost and no TL fuel surcharge | [R p.3]: "Driver cost is included in the linehaul rate; there is no extra hourly cost or fuel surcharge to add to TL" |
| No TL stop fees on LTL shipments; LTL uses no tractor | [R p.3]: "Do not add TL stop fees or allocate a listed tractor to LTL" |
| Unused tractor costs zero | [B p.3]; [R p.3] → enforced by C30–C31 when `tour_used[k,d] = 0` |
| Baseline (reporting, not a constraint): all-LTL `= Σ_i ltl_cost[i]` | [B p.3]: "The baseline is all-LTL… Report the savings against it in dollars and as a percentage" |

---

## 5. Step 3 – Constraints

*"What hinders me from making infinite revenues or incurring zero costs?"* Each constraint is listed with **the rule it enforces** and **where that rule appears** in the files ([B p.4]: "Say which rule each constraint enforces"). Unless stated otherwise, constraints hold for every `k ∈ TRACTORS` and `d ∈ DAYS`.

### 5.1 Assignment (service exactly once)

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C1** | `Σ_{k ∈ TRACTORS} on_tour[i,k,day[i]] + use_ltl[i] = 1   ∀ i ∈ SHIPMENTS` | Every shipment is served **exactly once**, by one truck tour or by LTL; shipments are not split | [B p.2, *Assignment*]: "Every shipment is served exactly once"; [R p.1]: "indivisible shipment"; [R p.6, §6]: "one service per shipment" |
| **C2** | `on_tour[i,k,d] = 0   ∀ i, k, d ≠ day[i]` | Pickup **and** delivery happen on the shipment's own day, so the tour must be that day's tour | [B p.2]: "both events occur on the shipment's stated day"; [R p.1, §1]: "Each shipment and each complete TL tour must finish on its stated day" |
| **C3** | `on_tour[i,k,d] ≤ tour_used[k,d]   ∀ i ∈ SHIPMENTS_ON_DAY[d]` | A shipment can only be on a tour that is selected | [B p.4]: "whether a tour is selected" (linking, fixed-charge logic from Module 3) |
| **C4** | `tour_used[k,d] ≤ Σ_{i ∈ SHIPMENTS_ON_DAY[d]} on_tour[i,k,d]` | A selected tour carries at least one shipment (no empty tours) | [B p.2]: "A dedicated tour carries one shipment"; [B p.3]: "An unused tractor costs nothing" |
| **C5** | `Σ_{i ∈ SHIPMENTS_ON_DAY[d]} on_tour[i,k,d] ≥ 2 · is_consolidated[k,d]` and `Σ_{i ∈ SHIPMENTS_ON_DAY[d]} on_tour[i,k,d] ≤ tour_used[k,d] + (floor(max_stops/2) − 1) · is_consolidated[k,d]` | Classifies the tour: consolidated ⇔ at least 2 shipments; dedicated ⇔ exactly 1 | [B p.2]: "A consolidated tour carries at least two shipments" |

### 5.2 Routing (flow and depot)

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C6** | `Σ_{v ∈ STOPS[d]} drive_leg[start_depot(k),v,k,d] = tour_used[k,d]` | If the tour is selected, the tractor leaves its **home depot** exactly once; it leaves at most once per day | [B p.2, *Routing*]: "starting… at the tractor's home depot"; [R p.3]: "At most one complete tour is allowed per pair per day"; [B p.5]: "one tour per tractor per day" |
| **C7** | `Σ_{u ∈ STOPS[d]} drive_leg[u,end_depot(k),k,d] = tour_used[k,d]` | The tour ends at the **same** home depot | [B p.2]: "…and ending at the tractor's home depot"; [R p.6, §6]: "final return to its designated depot" |
| **C8** | `Σ_{v : (u,v) ∈ LEGS[k,d]} drive_leg[u,v,k,d] = on_tour[i,k,d]` and `Σ_{v : (v,u) ∈ LEGS[k,d]} drive_leg[v,u,k,d] = on_tour[i,k,d]`   `∀ u ∈ {pickup(i), delivery(i)}, i ∈ SHIPMENTS_ON_DAY[d]` | Each pickup and delivery of an assigned shipment is visited exactly once, by **the same tractor** (flow conservation); the shipment stays on one trailer, with no hub | [B p.1]: "Nothing is unloaded at a hub: each shipment stays on that trailer from its pickup until its delivery" |
| **C9** | `drive_leg[u,v,k,d] ∈ {0,1}` only on `LEGS[k,d]` (no leg into `start_depot(k)`, none out of `end_depot(k)`, no leg `start_depot(k) → end_depot(k)`) | Depots are only start and end bases, never freight stops; no empty tour | [R p.3]: "Depots are bases, not pickup, delivery or storage locations"; [B p.4]: "This project does not move empty trailers between facilities" |

### 5.3 Timing

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C10** | `arrival[v,k,d] ≥ stop_departure[u,k,d] + drive_hours[u,v] − M_time[u,v] · (1 − drive_leg[u,v,k,d])   ∀ (u,v) ∈ LEGS[k,d]` (when `u = start_depot(k)`, use `depot_departure[k,d]`; when `v = end_depot(k)`, the left side is `return_time[k,d]`) | Arrival = previous departure + stored driving time. "Every extra stop makes every later arrival later." Also eliminates subtours (Big-M) | [B p.2]: "Every extra stop makes every later arrival later"; [R p.2]: travel data |
| **C11** | `service_start[u,k,d] ≥ arrival[u,k,d] + break_length · break_while_waiting[u,k,d]   ∀ u ∈ STOPS[d]` | Service cannot start before arrival; early trucks wait (waiting is allowed and is duty); a pre-service break needs 0.5 h of waiting | [R p.2]: "Early trucks may wait outside the site; waiting counts as driver duty" |
| **C12** | `stop_departure[u,k,d] ≥ service_start[u,k,d] + service_hours[u] + break_length · break_after_service[u,k,d]   ∀ u ∈ STOPS[d]` | The truck leaves only after the **full handling** (0.5 h) and any post-service break; handling is duty and is never a break | [R p.2]: "continuous handling time… Handling is duty, not a break"; [B p.5, *Checker*]: "handling" |
| **C13** | `service_start[u,k,d] ≥ earliest_start[u]   ∀ u ∈ STOPS[d]` | Service starts no earlier than **both** the window open and the facility open | [B p.2]: "picked up and delivered inside its own windows"; [R p.2]: "intersection of facility hours and the shipment's window" |
| **C14** | `service_start[u,k,d] + service_hours[u] ≤ latest_finish[u] + M_time · (1 − on_tour[i,k,d])   ∀ u ∈ {pickup(i), delivery(i)}` | Service **completes** by the window close **and** the facility close (may end exactly at closing). Hard, no penalty | [R p.1]: "latest… service completion. The full handling duration must fit"; [R p.2]: "Service may end exactly at closing"; [R p.6]: "No penalty buys permission to finish late"; [R p.1]: "capacities and service windows are hard constraints" |
| **C15** | `service_start[delivery(i),k,d] ≥ service_start[pickup(i),k,d] + service_hours[pickup(i)] − M_time · (1 − on_tour[i,k,d])   ∀ i ∈ SHIPMENTS_ON_DAY[d]` | **Pickup before delivery** | [B p.2]: "In both files, a shipment must be picked up before it is delivered"; [R p.6, §6]: "pickup-before-delivery" |
| **C16** | `depot_departure[k,d] ≥ tour_earliest_start[k,d]` | Leave the depot no earlier than the **shift start** and **depot opening** | [B p.2]: "depot hours, the driver's shift"; [R p.3]: "A tour must fit both depot hours and driver shift" |
| **C17** | `return_time[k,d] ≤ tour_latest_end[k,d]` | Return home by the **shift end** and **depot closing**, within the same day (no overnight reset) | [R p.4]: "No overnight reset during a tour is permitted: return home within the daily shift"; [R p.3] |
| **C18** | `return_time[k,d] ≤ 24 · horizon_end_day` | Everything finishes before the horizon end (00:00 Day 4 / Day 8) | [B p.3]: "The horizon ends at 00:00 on Day 4 / Day 8"; [R p.1, §1] |

### 5.4 Hours of service

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C19** | `Σ_{(u,v) ∈ LEGS[k,d]} drive_hours[u,v] · drive_leg[u,v,k,d] ≤ max_drive_hours · tour_used[k,d]` | **Max driving per tour** (8 h / 11 h), including depot approach and return | [B p.2 table]; [R p.3]: "total driving per tour… Include the approach, driving between stops, and the return"; [B p.5]: "an 8-hour drive limit" |
| **C20** | `return_time[k,d] − depot_departure[k,d] ≤ max_duty_hours · tour_used[k,d]` | **Max duty** (12 h / 14 h): elapsed departure-to-return time, including driving, handling, waiting and breaks | [B p.2]: "Duty is the elapsed time from leaving the depot until returning. It includes driving, handling, waiting, and any break"; [B p.5]: "a 12-hour duty limit" |
| **C21** | `depot_departure[k,d+1] − return_time[k,d] ≥ min_rest_hours − M · (2 − tour_used[k,d] − tour_used[k,d+1])   ∀ k, d < demand_days` | **Minimum rest** between daily tours (12 h / 10 h). Implied by the fixed shifts; kept so the model stays correct if shifts change | [B p.2 table]: "already provided by the shifts"; [R p.4]: "The supplied fixed repeating shifts already provide this rest. Recheck it if shifts are changed." |
| **C22** | `drive_since_break[v,k,d] ≥ drive_since_break[u,k,d] + drive_hours[u,v] − M · (break_while_waiting[u,k,d] + break_after_service[u,k,d]) − M · (1 − drive_leg[u,v,k,d])   ∀ (u,v) ∈ LEGS[k,d], u ≠ start_depot(k)` | Driving accumulates between breaks; a qualifying break at `u` resets the **within-tour** counter | [R p.3]: "break_hours… resetting that within-tour driving counter" |
| **C23** | `drive_since_break[v,k,d] ≥ drive_hours[u,v] − M · (1 − drive_leg[u,v,k,d])   ∀ (u,v) ∈ LEGS[k,d]` | The counter always includes the leg just driven (starts from the depot departure) | [R p.3] |
| **C24** | `drive_since_break[v,k,d] ≤ break_after_drive   ∀ v ∈ STOPS[d] ∪ {end_depot(k)}` | **Driver break:** take a 0.5 h break *before* further driving would exceed 8 h since the last break. This also covers the return leg | [R p.3]: "Take a break before further driving would exceed this threshold"; [B p.2 table]; [B p.5]: "the driver break" |
| **C25** | `break_while_waiting[u,k,d] + break_after_service[u,k,d] ≤ on_tour[i,k,d]   ∀ u ∈ {pickup(i), delivery(i)}` | Breaks only at a **stopped, visited** location, never while driving and never at an unvisited node | [R p.3]: "uninterrupted off-duty hours at a stopped location… can occur at a visited location" |

Breaks never reset total driving (C19) or duty (C20). C19 and C20 use totals, and break time is added to the elapsed duty through C12 ([R p.3]: "Breaks do not reset total daily driving or elapsed duty"; [B p.2]). No leg exceeds 8 h, so a break is never needed mid-leg ([R p.4]: "Travel lanes… can be driven between stopped locations without exceeding the eight-hour break threshold").

### 5.5 Capacity (three dimensions, after every event)

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C26** | `load_weight[v,k,d] ≥ load_weight[u,k,d] + weight_change[v] − cap_weight[equip[k]] · (1 − drive_leg[u,v,k,d])`, with `load_weight[start_depot(k),k,d] = 0` (and identically for `load_feet` with `feet_change`, `load_pallets` with `pallet_change`) | Load after each event = load before ± that shipment (additive; no stacking or packing) | [R p.2]: "Sum them for the freight on board after each event… additive requirements; do not infer extra capacity from stackability" |
| **C27** | `0 ≤ load_weight[u,k,d] ≤ cap_weight[equip[k]]`, `0 ≤ load_feet[u,k,d] ≤ cap_feet[equip[k]]`, `0 ≤ load_pallets[u,k,d] ≤ cap_pallets[equip[k]]` | **Weight, floor-length and pallet capacity** of the **assigned** equipment type, checked after every stop | [B p.1]: "Check weight, floor length, and pallets after every stop"; [R p.3]: "Use the assigned type, not the largest capacity in the table"; [B p.5]: "three capacities" |
| **C28** | Empty at departure (`load_* [start_depot(k),k,d] = 0`) and at return. Return emptiness is implied by C8 + C26 (every pickup on the tour has its delivery on the same tour) | No freight remains on the trailer when it returns home | [R p.3]: "No freight remains on the trailer when it returns home" |

### 5.6 Stop limit

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C29** | `Σ_{i ∈ SHIPMENTS_ON_DAY[d]} 2 · on_tour[i,k,d] ≤ max_stops · tour_used[k,d]` | At most `max_stops` service events per tour (6 → ≤ 3 shipments; 8 → ≤ 4 shipments) | [B p.2]: "the number of those events cannot exceed max_stops"; [R p.4]: "counting service events over the whole tour" |

### 5.7 Cost linearization (fixed charge and max{} terms)

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C30** | `linehaul_charge[k,d] ≥ min_linehaul[equip[k]] · tour_used[k,d]` and `linehaul_charge[k,d] ≥ rate_per_mile[equip[k]] · Σ_{(u,v)} miles[u,v] · drive_leg[u,v,k,d]` | Linehaul = `max{minimum, miles × rate}` over all miles, including empty depot legs. Valid because we minimize. Unused tour → `linehaul_charge = 0` | [R p.3]: "c = max{minimum linehaul, M × mileage rate} + …"; "All miles are charged, including empty depot legs"; "Unused tractors cost zero" |
| **C31** | `stop_charge[k,d] ≥ extra_stop_fee[equip[k]] · (Σ_i 2 · on_tour[i,k,d] − 2 · tour_used[k,d])` and `stop_charge[k,d] ≥ 0` | Stop-off = `max{0, N − 2} × fee`. A dedicated tour (N = 2) pays no stop-off charge | [R p.3]: "+ max{0, N − 2} × extra-stop fee"; [B p.2]: "A dedicated tour… has two service events and no extra-stop charge" |

### 5.8 Main-file extensions

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C32** | `on_tour[i,k,d] ≤ chilled_capable[equip[k]]   ∀ i : is_chilled[i] = 1` | **Refrigerated equipment:** chilled freight only on chilled-capable equipment (REEFER). Ambient freight may use either type | [B p.2]: "Chilled freight needs equipment with chilled_capable equal to 1"; [R p.3]: "Both types can carry ambient freight"; [R p.6, §5 item 2] |
| **C33** | `Σ_{m=1..6} pair_order[i,j,m,k,d] ≥ on_tour[i,k,d] + on_tour[j,k,d] − 1`, `Σ_m pair_order[i,j,m,k,d] ≤ on_tour[i,k,d]`, `Σ_m pair_order[i,j,m,k,d] ≤ on_tour[j,k,d]` | If both shipments are on the tour, exactly one event-order configuration holds; otherwise none | logical "choose exactly one" (Module 3) |
| **C34** | For each `m`, ordering links of the form `service_start[v,k,d] ≥ service_start[u,k,d] + service_hours[u] − M_time · (1 − pair_order[i,j,m,k,d])` for each consecutive pair `(u → v)` in configuration `m`: <br>m=1: `(delivery(i) → pickup(j))` · m=2: `(delivery(j) → pickup(i))` · m=3: `(pickup(i) → pickup(j))`, `(delivery(j) → delivery(i))` · m=4: `(pickup(j) → pickup(i))`, `(delivery(i) → delivery(j))` · m=5: `(pickup(i) → pickup(j))`, `(pickup(j) → delivery(i))`, `(delivery(i) → delivery(j))` · m=6: `(pickup(j) → pickup(i))`, `(pickup(i) → delivery(j))`, `(delivery(j) → delivery(i))` | Ties the chosen configuration to the actual timeline | — |
| **C35** | `pair_order[i,j,5,k,d] = pair_order[i,j,6,k,d] = 0` if `lifo_required = 1` | **Strict LIFO:** freight picked up later must come off first. No crossing pairs means the load order is a stack | [B p.2]: "Freight picked up later can block freight picked up earlier… strict last-in, first-out. Use it only when lifo_required is 1"; [R p.4]: "strict last-in, first-out, without rehandling"; [R p.6, §5 item 1] |
| **C36** | `pair_order[i,j,3,k,d] = pair_order[i,j,4,k,d] = pair_order[i,j,5,k,d] = pair_order[i,j,6,k,d] = 0   ∀ (i,j) ∈ INCOMPATIBLE_PAIRS[d]` | **Compatibility:** incompatible shipments (different temperature or group) are never on board at the same time. They may share the tractor sequentially (configurations 1 and 2) | [B p.2]: "Shipments on board together must have the same temperature and the same compatibility group"; [R p.2]: "Different groups may use the same tractor at different times after the previous freight has been delivered" |
| — | Different facility hours and narrower windows | Already inside C13–C14 through facility-specific `facility_open[f]`, `facility_close[f]` and shipment windows | [B p.5]: "different opening hours"; [R p.1]: "varied hours, narrower windows" |
| — | REEFER's smaller capacities | Already inside C27 through `equip[k]` | [R p.3]: "Main-file REEFER has capacities 42,000 lb, 48 feet and 24 pallet positions"; [R p.6]: "A reefer's smaller capacities also need to be checked" |
| — | Driver break | C22–C25 (§5.4) | [B p.5]: "the driver break" |

### 5.9 Variable domains

| ID | Constraint |
|---|---|
| **C37** | `use_ltl`, `on_tour`, `tour_used`, `is_consolidated`, `drive_leg`, `break_while_waiting`, `break_after_service`, `pair_order` ∈ {0,1} |
| **C38** | `depot_departure`, `arrival`, `return_time`, `service_start`, `stop_departure`, `drive_since_break`, `load_weight`, `load_feet`, `load_pallets`, `linehaul_charge`, `stop_charge` ≥ 0 (continuous) |

Source for C37: [B p.4]: "Binary variables are required where the choice is yes or no".

### 5.10 Compact model (summary)

```
minimize   Σ_{k,d} (linehaul_charge[k,d] + stop_charge[k,d]) + Σ_i ltl_cost[i] · use_ltl[i]

subject to C1–C5   (assignment)
           C6–C9   (routing)
           C10–C18 (timing)
           C19–C25 (hours of service)
           C26–C28 (capacity)
           C29     (stops)
           C30–C31 (cost)
           C32–C36 (main extensions)
           C37–C38 (domains)
```

**Decomposition by day:** because of C2 and C17, no variable or constraint links different days except C21, which is redundant with the supplied shifts. The model can therefore be solved day by day.

---

## 6. Formulation B – tour-selection model (solved in Gurobi)

This is the Module 3 camera set-cover model with "= 1" instead of "≥ 1": tours play the role of camera positions, and shipments play the role of the locations to cover.

**Sets.** `CANDIDATE_TOURS[k,d]` = candidate tours of tractor `k` on day `d`. Each tour `r ∈ CANDIDATE_TOURS[k,d]` is a fixed event sequence whose schedule satisfies **C6–C36** for that single tractor and day. `ALL_TOURS` = the union over all `(k,d)`.

**Parameters.** `tour_carries[i,r] = 1` if tour `r` carries shipment `i`; `tour_cost[r] = linehaul_charge + stop_charge` (C30–C31 evaluated for that route); `ltl_cost[i]` = LTL cost.

**Decision variables**

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **B-V1** | **`select_tour[r]`** | binary | 1 if candidate tour `r` is selected | [B p.4]: "whether a tour is selected" |
| **B-V2** | **`use_ltl[i]`** | binary | 1 if shipment `i` uses LTL | [B p.4]: "whether a shipment uses LTL" |

**Objective**
```
minimize   Σ_{r ∈ ALL_TOURS} tour_cost[r] · select_tour[r]  +  Σ_{i ∈ SHIPMENTS} ltl_cost[i] · use_ltl[i]
```

**Constraints**

| ID | Constraint | Rule | Source |
|---|---|---|---|
| **B1** | `Σ_{r ∈ ALL_TOURS} tour_carries[i,r] · select_tour[r] + use_ltl[i] = 1   ∀ i ∈ SHIPMENTS` | Served exactly once | [B p.2] |
| **B2** | `Σ_{r ∈ CANDIDATE_TOURS[k,d]} select_tour[r] ≤ 1   ∀ k ∈ TRACTORS, d ∈ DAYS` | One tour per tractor per day | [R p.3]; [B p.5] |
| **B3** | `select_tour[r]`, `use_ltl[i]` ∈ {0,1} | Binary yes/no choices | [B p.4] |
| **B4** | `r ∈ CANDIDATE_TOURS[k,d]` only if the route passes C6–C36 (checked in Python when the candidate is generated) | All routing, timing, capacity, HOS, LIFO and compatibility rules | [B p.2]: "every route can actually be driven" |

The timing, load and break variables of Formulation A (V5–V16) become **outputs** of each candidate's schedule. They are exported in the plan ([B p.4, *Deliverables 2*]: "the shipments on each tour, their order, the tractor, the times, and the cost").

**Optimality claim.** If `ALL_TOURS` contains **every** feasible tour (possible on the simplified file, with at most 3 shipments per tour), a zero gap is a global optimum. Otherwise it is optimal *over the given tours*, and the report must say how the candidates were limited ([B p.5]: "Call it a global optimum only if every feasible tour was included… On the main file, say how the candidate tours were limited").

---

## 7. Simplified vs main: which constraints are active

| Constraint group | Simplified | Main | Source |
|---|---|---|---|
| C1–C21, C26–C31 | ✅ (`max_drive_hours = 8`, `max_duty_hours = 12`, `min_rest_hours = 12`, `max_stops = 6`) | ✅ (`max_drive_hours = 11`, `max_duty_hours = 14`, `min_rest_hours = 10`, `max_stops = 8`) | [B p.2 table]; [R p.3–4] |
| Facility hours | identical at all facilities | facility-specific | [B p.3]: "Facility hours are the same at every facility" |
| Equipment | one type (DRY 45,000 lb / 53 ft / 26 pallets) | DRY + REEFER (42,000 / 48 / 24) | [R p.3] |
| C22–C25 break | inactive (break variables ≡ 0). With `max_drive_hours = 8 ≤ break_after_drive`, the counter can never exceed 8 | ✅ | [B p.2 table]: "not required by the 8 h drive cap" |
| C32 chilled | vacuous (all ambient) | ✅ | [B p.3]; [R p.1] |
| C35 LIFO | inactive (`lifo_required = 0`, side access: crossings allowed) | ✅ | [B p.2]; [R p.4] |
| C36 compatibility | vacuous (all compatible) | ✅ | [B p.3]: "the freight is compatible" |
| V13–V16 | can be omitted | required | — |

---

## 8. Rules deliberately not modeled

The files explicitly exclude these, so leaving them out is correct (and worth stating in the report):

| Item | Why there is no constraint | Source |
|---|---|---|
| Dock capacity and queues | "Handling staff and outside waiting space are sufficient, so no queue or dock-capacity model is required" | [R p.2] |
| 3-D packing and stacking | "do not infer extra capacity from stackability or attempt a separate packing problem" | [R p.2] |
| LTL capacity | "LTL is an outside carrier with enough capacity" | [B p.4]; [R p.1] |
| Empty trailer repositioning | "This project does not move empty trailers between facilities. That is Project 2" | [B p.4] |
| Holding or dynamic dispatch | "Holding a shipment for freight that is not in the workbook is not part of the assigned problem" | [B p.2]; [B p.5, *Optional extension*] |
| Driver hourly cost and TL fuel surcharge | "no extra hourly cost or fuel surcharge to add to TL" | [R p.3] |
| Depot parking limits | "Depot parking is sufficient" | [R p.3] |
| Trailer inventory | "Project 1 assumes the assigned TL equipment is available with each tractor" | [R p.4] |
| Extra demand or resources from the "Read me" / "Data dictionary" sheets | "contain explanations, not additional demand or resources" | [R p.4] |
| Shortest paths | "Use the given direct routes; no coordinates or shortest-path calculation are needed" | [R p.2] |
| Late-delivery penalties | "No penalty buys permission to finish late" (windows are hard) | [R p.6] |
| Extra calendar day | "A feasible tour in these files picks up and delivers on the shipment's own day" | [B p.5] |

---

## 9. Traceability matrix

Every rule-bearing sentence in both files, in reading order, mapped to the model element that handles it.

### 9.1 Project brief [B]

| Page / section | Statement in the file (abridged) | Model element |
|---|---|---|
| p.1 Context | Three options: LTL, dedicated TL, consolidation | V1 `use_ltl`, V2 `on_tour`, V3 `tour_used`, V4 `is_consolidated` |
| p.1 Context | "each shipment stays on that trailer from its pickup until its delivery" | C8 |
| p.1 Context | Extra stops, detours, waiting, space or hours can make a combination infeasible or more expensive than LTL | C10–C31 + objective |
| p.1 Why it is hard | "A shipment uses weight, floor length, and pallet positions at the same time" | V10–V12 (`load_weight`, `load_feet`, `load_pallets`), C26–C27 |
| p.1 Why it is hard | "Check weight, floor length, and pallets after every stop" | C26–C27 |
| p.2 | "picked up and delivered inside its own windows, and both events occur on the shipment's stated day" | C2, C13, C14 |
| p.2 | "Every extra stop makes every later arrival later" | C10 |
| p.2 | HOS limits read from driver_rules; differ by file | C19, C20, C21, C22–C24; §7 |
| p.2 table | Max driving 8 / 11 h | C19 |
| p.2 table | Max duty 12 / 14 h | C20 |
| p.2 table | Break 0.5 h (main) | C22–C25, C12, C11 |
| p.2 table | Rest 12 / 10 h, provided by shifts | C21 |
| p.2 | Duty = leaving depot → returning; includes driving, handling, waiting, break | C20, C11, C12 |
| p.2 | "A break does not reset the day's total driving or the duty clock" | C19, C20 (totals) |
| p.2 | Loading order: strict LIFO only when lifo_required = 1; side access otherwise | V16 `pair_order`, C35 |
| p.2 | "a shipment must be picked up before it is delivered" | C15 |
| p.2 | Static list; no holding | §8 |
| p.2 Problem statement | Choose dedicated / shared / LTL; minimize total cost; every route drivable | V1–V4; objective; B4 |
| p.2 Assignment | "Every shipment is served exactly once" | C1 / B1 |
| p.2 Routing | Stop sequence starting and ending at the home depot | V5 `drive_leg`, C6, C7, C9 |
| p.2 Timing | Leave depot, arrive at each stop, return | V6 `depot_departure`, V7 `arrival` / `return_time`, V8 `service_start`, V9 `stop_departure` |
| p.2 | Respect three capacities | C26–C27 |
| p.2 | Pickup and delivery windows | C13–C14 |
| p.2 | Facility hours | C13–C14 |
| p.2 | Depot hours | C16–C17 |
| p.2 | Driver's shift | C16–C17 |
| p.2 | Hours of service | C19–C25 |
| p.2 | Stop limit | C29 |
| p.2 | Loading order | C35 |
| p.2 | Freight compatibility: same temperature and group on board together | C36 |
| p.2 | "Chilled freight needs equipment with chilled_capable equal to 1" | C32 |
| p.2 | Dedicated = one shipment, two events, no extra-stop charge | C5, C31 |
| p.2 | Consolidated = at least two shipments | V4 `is_consolidated`, C5 |
| p.2 | Events ≤ max_stops (6 / 8; ≤ 3 / 4 shipments) | C29 |
| p.3 Objective | Linehaul + stop-off + LTL | Objective, V17 `linehaul_charge`, V18 `stop_charge` |
| p.3 | Cost uses the assigned tractor's equipment, every mile from departure to return | C30 |
| p.3 | "An unused tractor costs nothing" | C30–C31 with `tour_used = 0`; C4 |
| p.3 | "LTL does not use a listed tractor" | C1 (`use_ltl` has no tractor index) |
| p.3 | Baseline all-LTL; report savings | Reporting (§4) |
| p.3 Data provided | Horizon ends 00:00 Day 4 / Day 8 | C18 |
| p.3 | Simplified: same facility hours, one equipment type, compatible freight, side access | §7 |
| p.3 | Main: refrigerated, compatibility, strict loading order, driver break | C32, C36, C35, C22–C25 |
| p.4 | drive_hours stored; use it as given | parameter `drive_hours[u,v]` |
| p.4 | Absolute time = 24·day + hour; hours and shifts repeat daily | §2.5 |
| p.4 | LTL has enough capacity; no empty trailer moves | §8 |
| p.4 Deliverables 1 | Binaries for LTL choice and tour selection | V1 `use_ltl`, V3 `tour_used` / B-V1 `select_tour`, B-V2 `use_ltl` |
| p.4 Deliverables 2 | Output: shipments per tour, order, tractor, times, cost | V2, V5, V6–V9, V17–V18 |
| p.4–5 Grading | Simplified rules list: 3 capacities, windows, facility and depot hours, shifts, 8 h drive, 12 h duty, side access, one equipment type, one tour per tractor per day | C26–C27, C13–C14, C16–C17, C19, C20, §7, C6 / B2 |
| p.5 Grading | Main extensions: refrigerated, compatibility, strict LIFO, different opening hours, driver break | C32, C36, C35, C13–C14, C22–C25 |
| p.5 Gurobi | Global optimum only if every feasible tour was included | §6 |
| p.5 Results | Count LTL / dedicated / consolidated | V1 `use_ltl`, V4 `is_consolidated` |
| p.5 Results | Same-day delivery; no extra calendar day | C2, C17 |

### 9.2 Data README [R]

| Page / section | Statement in the file (abridged) | Model element |
|---|---|---|
| p.1 §1 | Each shipment **and** each TL tour finishes on its stated day | C2, C17 |
| p.1 §1 | Capacities and service windows are hard constraints | C14, C27 (no slack variables) |
| p.1 §1 | All-LTL baseline is feasible | §4 |
| p.1 §2 | Absolute time; days repeat; Day 0 planning | §2.5 |
| p.1 §2 | Joins: origin/destination → facilities; tractor → depot, equipment, tl_costs | §2.1, §2.3 |
| p.1 shipments | Indivisible shipment | C1, V2 `on_tour` binary |
| p.1 shipments | `day`: both pickup and delivery on that day | C2 |
| p.1 shipments | Window open = earliest service start; close = latest service completion; full handling must fit | C13, C14 |
| p.2 shipments | Weight, length, pallets: separate, additive, after each event; no stacking | C26–C27, §8 |
| p.2 shipments | Freight class only for LTL | `ltl_cost[i]` (§2.5) |
| p.2 shipments | Chilled needs chilled-capable equipment | C32 |
| p.2 shipments | Identical group **and** temperature on board together; different groups may share sequentially | C36 (and m = 1, 2 allowed) |
| p.2 shipments | Service hours 0.5; handling is duty, not a break | C12, C20, C25 |
| p.2 facilities | Intersection of facility hours and window | C13, C14 |
| p.2 facilities | Service may end exactly at closing | C14 (`≤`) |
| p.2 facilities | Early trucks may wait; waiting is duty | C11, C20 |
| p.2 facilities | No queue or dock capacity | §8 |
| p.2 distance | Directed; same location = 0; no reverse symmetry | `miles[u,v]`, `drive_hours[u,v]` |
| p.2 distance | Include depot approach and return in miles and driving time | C19, C30 |
| p.2 ltl_rates | Bands inclusive / exclusive; one match per shipment | `ltl_cost[i]`; input validation |
| p.2 ltl_rates | `ltl_cost = base · (1 + fuel)` | `ltl_cost[i]`; objective |
| p.3 ltl_rates | No TL stop fees on LTL; no tractor for LTL | Objective; C1 |
| p.3 tl_costs | `c = max{min, M × rate} + max{0, N − 2} × fee` | C30, C31 |
| p.3 tl_costs | All miles charged incl. empty legs; every pickup and delivery is an event even at a shared address; depot not an event | C30; `num_events[k,d]` (§3.8) |
| p.3 tl_costs | No hourly or fuel cost for TL; unused tractors cost zero | Objective; C30–C31 |
| p.3 equipment | Capacities per type; chilled flag; both carry ambient; use the **assigned** type | C27, C32 |
| p.3 tractors | Driver–tractor–equipment fixed | `equip[k]`, `home_depot[k]` parameters |
| p.3 tractors | At most one complete tour per pair per day; several sequential pickups and deliveries allowed | C6 / B2; V5 `drive_leg` |
| p.3 tractors | No freight remains when returning home | C28 |
| p.3 depots | Depots are bases only; parking sufficient | C9; §8 |
| p.3 depots | Tour fits depot hours and shift | C16, C17 |
| p.3 driver_rules | max_drive: includes approach, between stops, return | C19 |
| p.3 driver_rules | max_duty: departure to return, incl. handling, waiting, breaks | C20 |
| p.3 driver_rules | break_after_drive 8 h since last break; break before exceeding | C22–C24 |
| p.3 driver_rules | Break 0.5 h uninterrupted, at a stopped location; resets the within-tour counter; allowed after the last delivery | V13 `break_while_waiting`, V14 `break_after_service`, V15 `drive_since_break`, C11, C12, C22, C25 |
| p.3 driver_rules | Breaks do not reset daily driving or duty | C19, C20 |
| p.3–4 driver_rules | Handling cannot be a break; waiting only if recorded as off-duty rest | C12 (break after handling), C11 + V13 `break_while_waiting` (recorded pre-service break) |
| p.4 driver_rules | min_rest provided by shifts; recheck if shifts change | C21 |
| p.4 driver_rules | No overnight reset; return within the daily shift | C17 |
| p.4 driver_rules | No lane exceeds the 8 h threshold | justifies breaks only at nodes |
| p.4 parameters | demand_days, horizon_end_day | `DAYS`, C18 |
| p.4 parameters | time_step 0.25, speed 50 mph | already inside `drive_hours[u,v]` |
| p.4 parameters | max_stops counts service events over the whole tour | C29 |
| p.4 parameters | lifo_required: 0 side access / 1 strict LIFO without rehandling | C35 |
| p.4 parameters | No trailer inventory sheet | §8 |
| p.5 §4 | Worked example: capacity sum after each event, windows, facility hours, 7 h drive / 9 h duty, 4 events ≤ 6, cost CAD 1,000, LTL CAD 2,742.75 | Unit test for C13–C31 and `ltl_cost` |
| p.5 §4 | "Using T01 here can affect which other shipments it can serve that day; assess the whole assignment" | C6 / B2 (tractor-day is a shared resource) |
| p.6 §5.1 | LIFO example: S001 then S002 → deliver S002 first | C35 (configuration 3 allowed, 5 forbidden) |
| p.6 §5.2 | S006 chilled/industrial needs REEFER; cannot be on board with S001 | C32, C36 |
| p.6 §5.2 | Reefer's smaller capacities must be checked | C27 via `equip[k]` |
| p.6 §5.3 | Local hours, narrower windows; arrival 12:00 cannot finish by 12:00 | C13, C14 |
| p.6 §5.4 | Break example: 6 h + 4 h needs a break before exceeding 8; duty 12.5 h, drive 10 h | C22–C24, C19, C20 |
| p.6 §6 | Data checks (unique IDs, references, windows, one rate match) | Input validation (code) |
| p.6 §6 | Checker list: one service per shipment, pickup before delivery, compatible on-board loads, 3 capacities after every event, windows and facility hours, breaks and limits, equipment assignment, one tour per tractor per day, return to depot, recomputed cost | C1, C15, C36, C27, C13–C14, C19–C25, C27/C32, C6, C7, C30–C31 |

---

## 10. Verification log

The following checks were run on this file against the extracted text of both PDFs:

1. **Every constraint ID C1–C38 is defined exactly once** in §5, and every ID used in §6–§9 refers to a defined constraint.
2. **Every variable ID V1–V18 is defined** in §3 and used in at least one constraint or the objective.
3. **Sentence sweep of both source files.** Every sentence on every page containing a rule word (*must, cannot, only, exactly, at most, limit, maximum, require, need, before, after, each, every, not, no, within, allowed, permit, hard*) was extracted page by page and checked against §9. The pass found three non-binding items missing from the first draft, now added: miles not kilometres, driver names as labels (§2), and the "Read me" sheets (§8). Rule keywords covered: *window, capacity, weight, floor, pallet, shift, depot, duty, driving, break, rest, stop, LIFO / last-in, compatib, chilled, temperature, reefer, equipment, horizon, day, exactly once, minimum, extra-stop, miles, handling, waiting, facility hours, closing, tour per tractor*.
4. **Grading checklist** ([B p.4–5]) covered:
   - Simplified: three capacities → C26–C27 · windows → C13–C14 · facility and depot hours → C13, C14, C16, C17 · shifts → C16–C17 · 8 h drive → C19 · 12 h duty → C20 · side access → C35 inactive · one equipment type → C27 · one tour per tractor per day → C6 / B2.
   - Main: refrigerated → C32 · compatibility → C36 · strict LIFO → C35 · different opening hours → C13–C14 · driver break → C22–C25.
5. **Worked example check** ([R p.5]): route D01→F01→F02→F04→F03→D01. Loads after each event are 22,000 → 31,000 → 22,000 → 0 lb, all ≤ 45,000 (C27). Drive 7 ≤ 8 (C19). Duty 16:00 − 07:00 = 9 ≤ 12 (C20). Events 4 ≤ 6 (C29). `linehaul_charge = max(900, 350 × 2.20) = 900`, `stop_charge = 50 × (4 − 2) = 100`, cost CAD 1,000 (C30–C31). LIFO: configuration 3 (S002 nested in S001), allowed under C35.
6. **Readable-name rewrite check.** The previous version (math symbols) and this version were compared mechanically. Every section heading, every table row, every constraint ID (C1–C38, B1–B4), every variable ID (V1–V18, B-V1, B-V2) and every source citation and quote from the files appears in both. No old math symbol remains outside the §3.0 glossary.
