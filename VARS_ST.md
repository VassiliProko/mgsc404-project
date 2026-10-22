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

| Symbol | Meaning | Source |
|---|---|---|
| $S$, index $i, j$ | Shipments (one row per **indivisible** shipment) | [R p.1, *shipments*]: "one row per indivisible shipment" |
| $D=\{1,\dots,\text{demand\_days}\}$, index $d$ | Demand days (3 simplified, 7 main) | [R p.4, *parameters*]; [B p.3, *Data provided*] |
| $S_d \subseteq S$ | Shipments whose stated day is $d$ | [R p.1, *shipments*]: "day: the day on which both pickup and delivery must occur" |
| $K$, index $k$ | Tractor–driver pairs (8 simplified, 24 main) | [R p.3, *tractors and depots*] |
| $F$ | Facilities (12 simplified, 24 main) | [R p.2, *facilities*] |
| $O$ | Depots (2 simplified, 4 main) | [R p.3, *tractors and depots*] |
| $Q$ | Equipment types (DRY; main also REEFER) | [R p.3, *equipment*] |
| $i^+,\ i^-$ | Pickup node and delivery node of shipment $i$ | [B p.2, *Problem statement*]: "Each shipment adds one pickup and one delivery" |
| $N_d^+=\{i^+ : i\in S_d\}$, $N_d^-=\{i^- : i\in S_d\}$, $N_d = N_d^+\cup N_d^-$ | Service-event nodes on day $d$ | same |
| $0_k,\ \bar 0_k$ | Start and end copies of tractor $k$'s home depot | [B p.2, *Routing*]: "starting and ending at the tractor's home depot" |
| $V_{kd}=N_d\cup\{0_k,\bar 0_k\}$ | Nodes of tour $(k,d)$ | — |
| $A_{kd}$ | Arcs of tour $(k,d)$: $(0_k,v)$ for $v\in N_d$; $(u,v)$ for $u\ne v \in N_d$; $(u,\bar 0_k)$ for $u\in N_d$ | [R p.2, *distance_matrix*]: "complete directed travel data" |
| $P_d=\{(i,j): i<j,\ i,j\in S_d\}$ | Unordered shipment pairs on day $d$ | needed for loading order and compatibility (main) |
| $C_d\subseteq P_d$ | **Incompatible** pairs: different temperature **or** different compatibility group | [R p.2, *shipments*]: "Only shipments with identical groups and identical temperature requirements may be on board together" |

Depot nodes are only tour start and end points. No depot node appears in the middle of a tour ([R p.3]: "Depots are bases, not pickup, delivery or storage locations for freight").

---

## 2. Parameters

All values are read from the workbook, never hard-coded ([R p.6, §6]: "Read the workbook values rather than relying on row positions or example constants").

Units: CAD, **miles**, lb, feet, decimal hours ([R p.1, §2]: "Do not use the kilometre convention from Project 2"; [B p.4]). IDs are model keys. Driver names are reporting labels only ([R p.1, §2]: "IDs are model keys; driver names are reporting labels").

### 2.1 Location and travel
| Symbol | Meaning | Sheet / column | Source |
|---|---|---|---|
| $o_i,\ e_i$ | Origin and destination facility of $i$ | `shipments.origin_id`, `destination_id` | [R p.1] |
| $\text{loc}(u)$ | Location of node $u$: $\text{loc}(i^+)=o_i$, $\text{loc}(i^-)=e_i$, $\text{loc}(0_k)=\text{loc}(\bar0_k)=h_k$ | — | [R p.1, §2]: "Travel endpoints can be facilities or depots" |
| $\text{dist}_{uv}$ | Miles from $\text{loc}(u)$ to $\text{loc}(v)$ (**directed**); 0 if same location | `distance_matrix.distance_miles` | [R p.2]: "Same-location travel is zero and has no row. Do not assume a reverse lane has the same value." |
| $t_{uv}$ | Driving hours $\text{loc}(u)\to\text{loc}(v)$, **stored value** | `distance_matrix.drive_hours` | [B p.4]: "drive_hours is already stored in the sheet"; [R p.2] |

### 2.2 Shipments
| Symbol | Meaning | Column | Source |
|---|---|---|---|
| $d_i$ | Stated day of $i$ | `day` | [R p.1] |
| $\text{po}_i, \text{pc}_i$ | Pickup window: earliest service **start**, latest service **completion** | `pickup_open_hour`, `pickup_close_hour` | [R p.1]: "earliest pickup service start and latest pickup service completion. The full handling duration must fit." |
| $\text{do}_i, \text{dc}_i$ | Delivery window (same meaning) | `delivery_open_hour`, `delivery_close_hour` | [R p.1] |
| $\omega_i,\ \phi_i,\ \rho_i$ | Weight (lb), floor length (ft), pallets | `weight_lb`, `linear_feet`, `pallets` | [B p.1, *Why it is hard*]; [R p.2]: "three separate resource requirements" |
| $s_{i^+}, s_{i^-}$ | Handling time at pickup and at delivery (0.5 h) | `pickup_service_hours`, `delivery_service_hours` | [R p.2]: "continuous handling time at each event" |
| $\chi_i\in\{0,1\}$ | 1 if chilled | `temperature` | [R p.2]; [B p.2] |
| $\gamma_i$ | Compatibility group (general / food / industrial) | `compatibility_group` | [R p.2] |
| $\text{class}_i$ | Freight class (LTL pricing only) | `freight_class` | [R p.2]: "it does not replace physical capacity checks" |

### 2.3 Facilities, depots, tractors, equipment
| Symbol | Meaning | Column | Source |
|---|---|---|---|
| $\text{fo}_f, \text{fc}_f$ | Facility daily open and close | `facilities.open_hour`, `close_hour` | [R p.2, *facilities*] |
| $\text{oo}_h, \text{oc}_h$ | Depot daily open and close | `depots.open_hour`, `close_hour` | [R p.3] |
| $h_k$ | Home depot of $k$ | `tractors.home_depot_id` | [R p.3] |
| $q(k)$ | Equipment type of $k$ (fixed) | `tractors.equipment_id` | [R p.3]: "The driver–tractor–equipment assignment is fixed" |
| $\text{ss}_k, \text{se}_k$ | Daily shift start and end of $k$ | `shift_start_hour`, `shift_end_hour` | [R p.3] |
| $\bar W_q, \bar F_q, \bar P_q$ | Weight, length and pallet capacity of type $q$ | `equipment.*_capacity_*` | [R p.3, *equipment*] |
| $\kappa_q\in\{0,1\}$ | 1 if type $q$ is chilled-capable | `equipment.chilled_capable` | [R p.3]; [B p.2] |
| $c^{mi}_q,\ c^{min}_q,\ c^{st}_q$ | Linehaul CAD/mile, minimum linehaul, extra-stop fee | `tl_costs` | [R p.3, *tl_costs*] |

### 2.4 Driver rules and instance parameters
| Symbol | Meaning (simplified / main) | Source |
|---|---|---|
| $\bar D$ | `max_drive_hours` (8 / 11) | [R p.3]; [B p.2 table] |
| $\bar U$ | `max_duty_hours` (12 / 14) | [R p.3]; [B p.2 table] |
| $\bar G$ | `break_after_drive_hours` (8, main) | [R p.3] |
| $\eta$ | `break_hours` (0.5, main) | [R p.3] |
| $\bar r$ | `min_rest_hours` (12 / 10) | [R p.4] |
| $\bar n$ | `max_stops` (6 / 8 service events) | [R p.4]; [B p.2] |
| $\lambda^{LIFO}\in\{0,1\}$ | `lifo_required` (0 / 1) | [R p.4]; [B p.2] |
| $H^{end}$ | `horizon_end_day` (4 / 8; exclusive at hour 0) | [R p.4]; [B p.3] |

### 2.5 Derived parameters (pre-computed in Python)
| Symbol | Formula | Source |
|---|---|---|
| Absolute time | $24\cdot\text{day}+\text{hour}$ | [B p.4]; [R p.1, §2] |
| Earliest service start at node $u$: $\underline{E}_u$ | $u=i^+$: $24d_i+\max(\text{po}_i,\text{fo}_{o_i})$ · $u=i^-$: $24d_i+\max(\text{do}_i,\text{fo}_{e_i})$ | [R p.2]: "Service must satisfy the intersection of facility hours and the shipment's window; neither replaces the other." |
| Latest service completion at $u$: $\overline{L}_u$ | $u=i^+$: $24d_i+\min(\text{pc}_i,\text{fc}_{o_i})$ · $u=i^-$: $24d_i+\min(\text{dc}_i,\text{fc}_{e_i})$ | same; [R p.2]: "Service may end exactly at closing" (so $\le$) |
| Tour start and end bounds | $\underline{T}_{kd}=24d+\max(\text{ss}_k,\text{oo}_{h_k})$, $\overline{T}_{kd}=24d+\min(\text{se}_k,\text{oc}_{h_k})$ | [R p.3]: "A tour must fit both depot hours and driver shift" |
| Load change at node $u$ | $\Delta^W_{i^+}=+\omega_i,\ \Delta^W_{i^-}=-\omega_i$ (same for $\phi,\rho$) | [R p.2]: "Sum them for the freight on board after each event" |
| LTL cost $l_i$ | $l_i=\text{base}_i\,(1+\text{fuel}_i)$, using the unique band matching class, weight and direct distance $\text{dist}(o_i,e_i)$; lower bounds inclusive, upper bounds exclusive | [R p.2–3, *ltl_rates*] |
| Big-M values | Time: $M^T_{uv}=\overline{L}_u+t_{uv}-\underline{E}_v$ (or 24). Loads: the capacity. | class Big-M method (Module 3) |

---

## 3. Step 1 – Decision variables

*"What decision(s) must be made in order to solve the problem?"* The brief answers this directly: **Assignment, Routing, Timing** ([B p.2, *Problem statement*]). All tour-level variables carry the index pair $(k,d)$, meaning tractor $k$'s tour on day $d$. A shipment $i$ can only be on a day-$d_i$ tour.

### 3.1 Assignment variables

| # | Variable | Type | Meaning | Source in the files |
|---|---|---|---|---|
| **V1** | $z_i$ | binary | 1 if shipment $i$ is sent by **LTL** | [B p.4, *Deliverables 1*]: "Binary variables are required where the choice is yes or no, **including whether a shipment uses LTL**"; [B p.2, *Problem statement*]: "choose a dedicated truckload, a shared multi-stop truckload, or LTL" |
| **V2** | $y_i^{kd}$ | binary | 1 if shipment $i$ is carried on tractor $k$'s tour on day $d$ | [B p.2, *Assignment*]: "Which shipments share a truck, which move alone on a dedicated truckload"; [R p.1]: "indivisible shipment"; [B p.1]: "each shipment stays on that trailer from its pickup until its delivery" |
| **V3** | $w^{kd}$ | binary | 1 if tractor $k$ performs a tour on day $d$ (**tour selected**) | [B p.4, *Deliverables 1*]: "…**and whether a tour is selected**"; [B p.3]: "An unused tractor costs nothing"; [R p.3]: "At most one complete tour is allowed per pair per day" |
| **V4** | $\delta^{kd}$ | binary | 1 if tour $(k,d)$ is **consolidated** (≥ 2 shipments); $w^{kd}-\delta^{kd}=1$ means a **dedicated** tour | [B p.2]: "A dedicated tour carries one shipment… A consolidated tour carries at least two shipments"; [B p.5, *Results*]: "how many shipments are on LTL, on a dedicated tour, and on a tour with at least two shipments" |

### 3.2 Routing variables

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **V5** | $x_{uv}^{kd}$, $(u,v)\in A_{kd}$ | binary | 1 if tractor $k$ on day $d$ drives directly from node $u$ to node $v$ | [B p.2, *Routing*]: "The stop sequence for each truckload, starting and ending at the tractor's home depot"; [B p.4, *Deliverables 2*]: "their order" |

### 3.3 Timing variables

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **V6** | $\ell_{0_k}^{kd}$ | continuous | Time tractor $k$ **leaves** its depot on day $d$ | [B p.2, *Timing*]: "When the tractor leaves the depot" |
| **V7** | $a_u^{kd}$, $u\in N_d\cup\{\bar0_k\}$ | continuous | **Arrival** time at node $u$; $a_{\bar0_k}^{kd}$ is the **return** time | [B p.2, *Timing*]: "when it arrives at each stop, and when it returns" |
| **V8** | $b_u^{kd}$, $u\in N_d$ | continuous | **Service start** time at node $u$ | [R p.1]: windows bound "service start" and "service completion"; [R p.2]: "Early trucks may wait outside the site" |
| **V9** | $\ell_u^{kd}$, $u\in N_d$ | continuous | **Departure** time from node $u$ (after handling and any post-service break) | needed so a break can be taken "after the last delivery before returning home" [R p.3] |

### 3.4 Load variables (three capacity dimensions)

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **V10** | $Q^{W,kd}_u$ | continuous ≥ 0 | Weight on board **after** service at $u$ | [B p.1]: "Check weight, floor length, and pallets after every stop"; [R p.2]: "Sum them for the freight on board after each event" |
| **V11** | $Q^{F,kd}_u$ | continuous ≥ 0 | Floor length (ft) on board after $u$ | same |
| **V12** | $Q^{P,kd}_u$ | continuous ≥ 0 | Pallet positions on board after $u$ | same |

### 3.5 Driver-break variables (main file)

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **V13** | $\beta^{\text{pre},kd}_u$ | binary | 1 if a 0.5 h break is taken at $u$ **while waiting before service** | [R p.3–4]: "waiting can count only if explicitly recorded as uninterrupted off-duty rest" |
| **V14** | $\beta^{\text{post},kd}_u$ | binary | 1 if a 0.5 h break is taken at $u$ **after service** | [R p.3]: "A break can occur at a visited location, including after the last delivery before returning home" |
| **V15** | $g^{kd}_u$ | continuous ≥ 0 | Driving accumulated **since the last qualifying break**, on arrival at $u$ | [R p.3]: "break_after_drive_hours: 8 hours of accumulated driving since the last qualifying break" |

### 3.6 Loading-order and compatibility variables (main file)

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **V16** | $\pi^{m,kd}_{ij}$, $(i,j)\in P_d$, $m=1..6$ | binary | 1 if $i$ and $j$ are both on tour $(k,d)$ and their four events occur in **configuration $m$** (table below) | [B p.2]: "strict last-in, first-out"; [R p.2]: "Different groups may use the same tractor at different times after the previous freight has been delivered" |

| $m$ | Event order | Name | Allowed when |
|---|---|---|---|
| 1 | $i^+ \to i^- \to j^+ \to j^-$ | $i$ entirely before $j$ | always |
| 2 | $j^+ \to j^- \to i^+ \to i^-$ | $j$ entirely before $i$ | always |
| 3 | $i^+ \to j^+ \to j^- \to i^-$ | $j$ nested in $i$ (LIFO-consistent) | pair compatible |
| 4 | $j^+ \to i^+ \to i^- \to j^-$ | $i$ nested in $j$ (LIFO-consistent) | pair compatible |
| 5 | $i^+ \to j^+ \to i^- \to j^-$ | crossing (violates LIFO) | compatible **and** side access ($\lambda^{LIFO}=0$) |
| 6 | $j^+ \to i^+ \to j^- \to i^-$ | crossing (violates LIFO) | compatible **and** side access |

These are the only 6 orders of four events in which each pickup precedes its own delivery ($4!/2^2=6$). This is the class "choose exactly one mode" construction (Module 3, *Multi-Value Choices*, item 4).

### 3.7 Cost variables

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **V17** | $H^{kd}$ | continuous ≥ 0 | Linehaul charge of tour $(k,d)$ $=\max\{c^{min},\ c^{mi}\cdot\text{miles}\}$ (linearized) | [R p.3, *tl_costs*]; [B p.3, *Objective*] |
| **V18** | $E^{kd}$ | continuous ≥ 0 | Stop-off charge $=\max\{0, N-2\}\cdot c^{st}$ (linearized) | [R p.3, *tl_costs*]; [B p.3]: "stop-off charges" |

### 3.8 Derived quantities (linear expressions, not extra variables)

| Expression | Meaning | Source |
|---|---|---|
| $\text{Mi}^{kd}=\sum_{(u,v)\in A_{kd}}\text{dist}_{uv}x^{kd}_{uv}$ | Tour miles, **including empty depot legs** | [R p.3]: "All miles are charged, including empty depot legs"; [B p.3]: "every mile from the depot departure through the return" |
| $N^{kd}=\sum_{i\in S_d}2\,y_i^{kd}$ | Service events (each pickup and delivery counts, even at a shared address; depot departure and return do not count) | [R p.3, *tl_costs*] |
| $\text{Dr}^{kd}=\sum_{(u,v)}t_{uv}x^{kd}_{uv}$ | Total driving, including approach and return | [R p.3]: "Include the approach, driving between stops, and the return" |
| $\text{Du}^{kd}=a^{kd}_{\bar0_k}-\ell^{kd}_{0_k}$ | Duty = elapsed departure-to-return time | [B p.2]: "Duty is the elapsed time from leaving the depot until returning" |
| $b_u-a_u$ | Waiting at $u$ (counts as duty) | [R p.2]: "waiting counts as driver duty" |
| $H^{kd}+E^{kd}$ | Truckload cost of the tour | [R p.3] |

**Count:** 18 variable families (V1–V18). The brief's required binaries are V1 ($z$, LTL) and V3 ($w$, tour selected) in Formulation A, and $z_i$ and $\lambda_r$ in Formulation B.

---

## 4. Step 2 – Objective function

*Minimize total transportation cost* ([B p.2, *Problem statement*]: "so that the total transportation cost is as small as possible").

$$
\min\ \ \underbrace{\sum_{k\in K}\sum_{d\in D} H^{kd}}_{\text{linehaul of TL tours}}
\;+\;\underbrace{\sum_{k\in K}\sum_{d\in D} E^{kd}}_{\text{stop-off charges}}
\;+\;\underbrace{\sum_{i\in S} l_i\, z_i}_{\text{LTL cost of shipments left on LTL}}
$$

| Term | Source |
|---|---|
| Three-part structure | [B p.3, *Objective*]: "total cost = Σ linehaul cost of truckload tours + Σ stop-off charges + Σ LTL cost of shipments left on LTL" |
| $H, E$ use the **assigned tractor's** equipment rates | [B p.3]: "The cost uses the equipment of the assigned tractor" |
| No hourly driver cost and no TL fuel surcharge | [R p.3]: "Driver cost is included in the linehaul rate; there is no extra hourly cost or fuel surcharge to add to TL" |
| No TL stop fees on LTL shipments; LTL uses no tractor | [R p.3]: "Do not add TL stop fees or allocate a listed tractor to LTL" |
| Unused tractor costs zero | [B p.3]; [R p.3] → enforced by C30–C31 when $w^{kd}=0$ |
| Baseline (reporting, not a constraint): all-LTL $=\sum_i l_i$ | [B p.3]: "The baseline is all-LTL… Report the savings against it in dollars and as a percentage" |

---

## 5. Step 3 – Constraints

*"What hinders me from making infinite revenues or incurring zero costs?"* Each constraint is listed with **the rule it enforces** and **where that rule appears** in the files ([B p.4]: "Say which rule each constraint enforces"). Unless stated otherwise, constraints hold for every $k\in K$ and $d\in D$.

### 5.1 Assignment (service exactly once)

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C1** | $\displaystyle\sum_{k\in K} y_i^{k,d_i} + z_i = 1 \quad \forall i\in S$ | Every shipment is served **exactly once**, by one truck tour or by LTL; shipments are not split | [B p.2, *Assignment*]: "Every shipment is served exactly once"; [R p.1]: "indivisible shipment"; [R p.6, §6]: "one service per shipment" |
| **C2** | $y_i^{kd}=0 \quad \forall i,k,\ d\ne d_i$ | Pickup **and** delivery happen on the shipment's own day, so the tour must be that day's tour | [B p.2]: "both events occur on the shipment's stated day"; [R p.1, §1]: "Each shipment and each complete TL tour must finish on its stated day" |
| **C3** | $y_i^{kd}\le w^{kd} \quad \forall i\in S_d$ | A shipment can only be on a tour that is selected | [B p.4]: "whether a tour is selected" (linking, fixed-charge logic from Module 3) |
| **C4** | $\displaystyle w^{kd}\le\sum_{i\in S_d} y_i^{kd}$ | A selected tour carries at least one shipment (no empty tours) | [B p.2]: "A dedicated tour carries one shipment"; [B p.3]: "An unused tractor costs nothing" |
| **C5** | $\displaystyle\sum_{i\in S_d}y_i^{kd}\ \ge\ 2\,\delta^{kd},\qquad \sum_{i\in S_d}y_i^{kd}\ \le\ w^{kd}+(\lfloor\bar n/2\rfloor-1)\,\delta^{kd}$ | Classifies the tour: consolidated ⇔ at least 2 shipments; dedicated ⇔ exactly 1 | [B p.2]: "A consolidated tour carries at least two shipments" |

### 5.2 Routing (flow and depot)

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C6** | $\displaystyle\sum_{v\in N_d}x^{kd}_{0_k v}=w^{kd}$ | If the tour is selected, the tractor leaves its **home depot** exactly once; it leaves at most once per day | [B p.2, *Routing*]: "starting… at the tractor's home depot"; [R p.3]: "At most one complete tour is allowed per pair per day"; [B p.5]: "one tour per tractor per day" |
| **C7** | $\displaystyle\sum_{u\in N_d}x^{kd}_{u\bar0_k}=w^{kd}$ | The tour ends at the **same** home depot | [B p.2]: "…and ending at the tractor's home depot"; [R p.6, §6]: "final return to its designated depot" |
| **C8** | $\displaystyle\sum_{v:(u,v)\in A_{kd}}x^{kd}_{uv}=y_i^{kd},\quad \sum_{v:(v,u)\in A_{kd}}x^{kd}_{vu}=y_i^{kd}\quad \forall u\in\{i^+,i^-\},\ i\in S_d$ | Each pickup and delivery of an assigned shipment is visited exactly once, by **the same tractor** (flow conservation); the shipment stays on one trailer, with no hub | [B p.1]: "Nothing is unloaded at a hub: each shipment stays on that trailer from its pickup until its delivery" |
| **C9** | $x^{kd}_{uv}\in\{0,1\}$ only on $A_{kd}$ (no arc into $0_k$, none out of $\bar0_k$, no arc $0_k\to\bar0_k$) | Depots are only start and end bases, never freight stops; no empty tour | [R p.3]: "Depots are bases, not pickup, delivery or storage locations"; [B p.4]: "This project does not move empty trailers between facilities" |

### 5.3 Timing

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C10** | $a^{kd}_v\ \ge\ \ell^{kd}_u + t_{uv} - M^T_{uv}\,(1-x^{kd}_{uv})\quad\forall (u,v)\in A_{kd}$ (with $u=0_k$ using $\ell^{kd}_{0_k}$) | Arrival = previous departure + stored driving time. "Every extra stop makes every later arrival later." Also eliminates subtours (Big-M) | [B p.2]: "Every extra stop makes every later arrival later"; [R p.2]: travel data |
| **C11** | $b^{kd}_u\ \ge\ a^{kd}_u + \eta\,\beta^{\text{pre},kd}_u\quad\forall u\in N_d$ | Service cannot start before arrival; early trucks wait (waiting is allowed and is duty); a pre-service break needs 0.5 h of waiting | [R p.2]: "Early trucks may wait outside the site; waiting counts as driver duty" |
| **C12** | $\ell^{kd}_u\ \ge\ b^{kd}_u + s_u + \eta\,\beta^{\text{post},kd}_u\quad\forall u\in N_d$ | The truck leaves only after the **full handling** (0.5 h) and any post-service break; handling is duty and is never a break | [R p.2]: "continuous handling time… Handling is duty, not a break"; [B p.5, *Checker*]: "handling" |
| **C13** | $b^{kd}_u\ \ge\ \underline{E}_u\quad\forall u\in N_d$ | Service starts no earlier than **both** the window open and the facility open | [B p.2]: "picked up and delivered inside its own windows"; [R p.2]: "intersection of facility hours and the shipment's window" |
| **C14** | $b^{kd}_u + s_u\ \le\ \overline{L}_u + M^T(1-y_i^{kd})\quad\forall u\in\{i^+,i^-\}$ | Service **completes** by the window close **and** the facility close (may end exactly at closing). Hard, no penalty | [R p.1]: "latest… service completion. The full handling duration must fit"; [R p.2]: "Service may end exactly at closing"; [R p.6]: "No penalty buys permission to finish late"; [R p.1]: "capacities and service windows are hard constraints" |
| **C15** | $b^{kd}_{i^-}\ \ge\ b^{kd}_{i^+}+s_{i^+} - M^T(1-y_i^{kd})\quad\forall i\in S_d$ | **Pickup before delivery** | [B p.2]: "In both files, a shipment must be picked up before it is delivered"; [R p.6, §6]: "pickup-before-delivery" |
| **C16** | $\ell^{kd}_{0_k}\ \ge\ \underline{T}_{kd}$ | Leave the depot no earlier than the **shift start** and **depot opening** | [B p.2]: "depot hours, the driver's shift"; [R p.3]: "A tour must fit both depot hours and driver shift" |
| **C17** | $a^{kd}_{\bar0_k}\ \le\ \overline{T}_{kd}$ | Return home by the **shift end** and **depot closing**, within the same day (no overnight reset) | [R p.4]: "No overnight reset during a tour is permitted: return home within the daily shift"; [R p.3] |
| **C18** | $a^{kd}_{\bar0_k}\ \le\ 24\,H^{end}$ | Everything finishes before the horizon end (00:00 Day 4 / Day 8) | [B p.3]: "The horizon ends at 00:00 on Day 4 / Day 8"; [R p.1, §1] |

### 5.4 Hours of service

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C19** | $\displaystyle\sum_{(u,v)\in A_{kd}}t_{uv}\,x^{kd}_{uv}\ \le\ \bar D\, w^{kd}$ | **Max driving per tour** (8 h / 11 h), including depot approach and return | [B p.2 table]; [R p.3]: "total driving per tour… Include the approach, driving between stops, and the return"; [B p.5]: "an 8-hour drive limit" |
| **C20** | $a^{kd}_{\bar0_k}-\ell^{kd}_{0_k}\ \le\ \bar U\, w^{kd}$ | **Max duty** (12 h / 14 h): elapsed departure-to-return time, including driving, handling, waiting and breaks | [B p.2]: "Duty is the elapsed time from leaving the depot until returning. It includes driving, handling, waiting, and any break"; [B p.5]: "a 12-hour duty limit" |
| **C21** | $\ell^{k,d+1}_{0_k}-a^{kd}_{\bar0_k}\ \ge\ \bar r - M(2-w^{kd}-w^{k,d+1})\quad \forall k,\ d<\text{demand\_days}$ | **Minimum rest** between daily tours (12 h / 10 h). Implied by the fixed shifts; kept so the model stays correct if shifts change | [B p.2 table]: "already provided by the shifts"; [R p.4]: "The supplied fixed repeating shifts already provide this rest. Recheck it if shifts are changed." |
| **C22** | $g^{kd}_v\ \ge\ g^{kd}_u + t_{uv} - M\big(\beta^{\text{pre},kd}_u+\beta^{\text{post},kd}_u\big) - M(1-x^{kd}_{uv})\quad\forall (u,v)\in A_{kd},\ u\ne0_k$ | Driving accumulates between breaks; a qualifying break at $u$ resets the **within-tour** counter | [R p.3]: "break_hours… resetting that within-tour driving counter" |
| **C23** | $g^{kd}_v\ \ge\ t_{uv} - M(1-x^{kd}_{uv})\quad\forall (u,v)\in A_{kd}$ | The counter always includes the leg just driven (starts from the depot departure) | [R p.3] |
| **C24** | $g^{kd}_v\ \le\ \bar G\quad\forall v\in N_d\cup\{\bar0_k\}$ | **Driver break:** take a 0.5 h break *before* further driving would exceed 8 h since the last break. This also covers the return leg | [R p.3]: "Take a break before further driving would exceed this threshold"; [B p.2 table]; [B p.5]: "the driver break" |
| **C25** | $\beta^{\text{pre},kd}_u+\beta^{\text{post},kd}_u\ \le\ y_i^{kd}\quad\forall u\in\{i^+,i^-\}$ | Breaks only at a **stopped, visited** location, never while driving and never at an unvisited node | [R p.3]: "uninterrupted off-duty hours at a stopped location… can occur at a visited location" |

Breaks never reset total driving (C19) or duty (C20). C19 and C20 use totals, and break time is added to the elapsed duty through C12 ([R p.3]: "Breaks do not reset total daily driving or elapsed duty"; [B p.2]). No leg exceeds 8 h, so a break is never needed mid-leg ([R p.4]: "Travel lanes… can be driven between stopped locations without exceeding the eight-hour break threshold").

### 5.5 Capacity (three dimensions, after every event)

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C26** | $Q^{W,kd}_v\ \ge\ Q^{W,kd}_u + \Delta^W_v - \bar W_{q(k)}(1-x^{kd}_{uv})$, with $Q^{W,kd}_{0_k}=0$ (and identically for $Q^F$ with $\Delta^F$, $Q^P$ with $\Delta^P$) | Load after each event = load before ± that shipment (additive; no stacking or packing) | [R p.2]: "Sum them for the freight on board after each event… additive requirements; do not infer extra capacity from stackability" |
| **C27** | $0\le Q^{W,kd}_u\le\bar W_{q(k)},\quad 0\le Q^{F,kd}_u\le\bar F_{q(k)},\quad 0\le Q^{P,kd}_u\le\bar P_{q(k)}$ | **Weight, floor-length and pallet capacity** of the **assigned** equipment type, checked after every stop | [B p.1]: "Check weight, floor length, and pallets after every stop"; [R p.3]: "Use the assigned type, not the largest capacity in the table"; [B p.5]: "three capacities" |
| **C28** | Empty at departure ($Q_{0_k}=0$) and at return. Return emptiness is implied by C8 + C26 (every pickup on the tour has its delivery on the same tour) | No freight remains on the trailer when it returns home | [R p.3]: "No freight remains on the trailer when it returns home" |

### 5.6 Stop limit

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C29** | $\displaystyle\sum_{i\in S_d}2\,y_i^{kd}\ \le\ \bar n\,w^{kd}$ | At most `max_stops` service events per tour (6 → ≤ 3 shipments; 8 → ≤ 4 shipments) | [B p.2]: "the number of those events cannot exceed max_stops"; [R p.4]: "counting service events over the whole tour" |

### 5.7 Cost linearization (fixed charge and max{} terms)

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C30** | $H^{kd}\ \ge\ c^{min}_{q(k)}\,w^{kd},\qquad H^{kd}\ \ge\ c^{mi}_{q(k)}\displaystyle\sum_{(u,v)}\text{dist}_{uv}\,x^{kd}_{uv}$ | Linehaul $=\max\{\text{minimum},\ \text{miles}\times\text{rate}\}$ over all miles, including empty depot legs. Valid because we minimize. Unused tour → $H=0$ | [R p.3]: "c = max{minimum linehaul, M × mileage rate} + …"; "All miles are charged, including empty depot legs"; "Unused tractors cost zero" |
| **C31** | $E^{kd}\ \ge\ c^{st}_{q(k)}\Big(\displaystyle\sum_{i}2y_i^{kd}-2w^{kd}\Big),\qquad E^{kd}\ge0$ | Stop-off $=\max\{0,N-2\}\times$fee. A dedicated tour (N = 2) pays no stop-off charge | [R p.3]: "+ max{0, N − 2} × extra-stop fee"; [B p.2]: "A dedicated tour… has two service events and no extra-stop charge" |

### 5.8 Main-file extensions

| ID | Constraint | Rule enforced | Source |
|---|---|---|---|
| **C32** | $y_i^{kd}\ \le\ \kappa_{q(k)}\quad\forall i:\chi_i=1$ | **Refrigerated equipment:** chilled freight only on chilled-capable equipment (REEFER). Ambient freight may use either type | [B p.2]: "Chilled freight needs equipment with chilled_capable equal to 1"; [R p.3]: "Both types can carry ambient freight"; [R p.6, §5 item 2] |
| **C33** | $\displaystyle\sum_{m=1}^{6}\pi^{m,kd}_{ij}\ \ge\ y_i^{kd}+y_j^{kd}-1,\quad \sum_{m}\pi^{m,kd}_{ij}\le y_i^{kd},\quad \sum_{m}\pi^{m,kd}_{ij}\le y_j^{kd}$ | If both shipments are on the tour, exactly one event-order configuration holds; otherwise none | logical "choose exactly one" (Module 3) |
| **C34** | For each $m$, ordering links of the form $b_{v}\ \ge\ b_{u}+s_u - M^T(1-\pi^{m,kd}_{ij})$ for each consecutive pair in configuration $m$: <br>m=1: $(i^-\!\to j^+)$ · m=2: $(j^-\!\to i^+)$ · m=3: $(i^+\!\to j^+),(j^-\!\to i^-)$ · m=4: $(j^+\!\to i^+),(i^-\!\to j^-)$ · m=5: $(i^+\!\to j^+),(j^+\!\to i^-),(i^-\!\to j^-)$ · m=6: $(j^+\!\to i^+),(i^+\!\to j^-),(j^-\!\to i^-)$ | Ties the chosen configuration to the actual timeline | — |
| **C35** | $\pi^{5,kd}_{ij}=\pi^{6,kd}_{ij}=0$ if $\lambda^{LIFO}=1$ | **Strict LIFO:** freight picked up later must come off first. No crossing pairs means the load order is a stack | [B p.2]: "Freight picked up later can block freight picked up earlier… strict last-in, first-out. Use it only when lifo_required is 1"; [R p.4]: "strict last-in, first-out, without rehandling"; [R p.6, §5 item 1] |
| **C36** | $\pi^{3,kd}_{ij}=\pi^{4,kd}_{ij}=\pi^{5,kd}_{ij}=\pi^{6,kd}_{ij}=0\quad\forall (i,j)\in C_d$ | **Compatibility:** incompatible shipments (different temperature or group) are never on board at the same time. They may share the tractor sequentially (configurations 1 and 2) | [B p.2]: "Shipments on board together must have the same temperature and the same compatibility group"; [R p.2]: "Different groups may use the same tractor at different times after the previous freight has been delivered" |
| — | Different facility hours and narrower windows | Already inside C13–C14 through facility-specific $\text{fo}_f,\text{fc}_f$ and shipment windows | [B p.5]: "different opening hours"; [R p.1]: "varied hours, narrower windows" |
| — | REEFER's smaller capacities | Already inside C27 through $q(k)$ | [R p.3]: "Main-file REEFER has capacities 42,000 lb, 48 feet and 24 pallet positions"; [R p.6]: "A reefer's smaller capacities also need to be checked" |
| — | Driver break | C22–C25 (§5.4) | [B p.5]: "the driver break" |

### 5.9 Variable domains

| ID | Constraint |
|---|---|
| **C37** | $z_i,\ y_i^{kd},\ w^{kd},\ \delta^{kd},\ x^{kd}_{uv},\ \beta^{\text{pre},kd}_u,\ \beta^{\text{post},kd}_u,\ \pi^{m,kd}_{ij}\in\{0,1\}$ |
| **C38** | $a,\ b,\ \ell,\ g,\ Q^W,\ Q^F,\ Q^P,\ H,\ E\ \ge 0$ (continuous) |

Source for C37: [B p.4]: "Binary variables are required where the choice is yes or no".

### 5.10 Compact model (summary)

$$
\begin{aligned}
\min\ & \textstyle\sum_{k,d}(H^{kd}+E^{kd})+\sum_i l_i z_i\\
\text{s.t. }& \text{C1–C5 (assignment)},\ \text{C6–C9 (routing)},\ \text{C10–C18 (timing)},\ \text{C19–C25 (hours of service)},\\
& \text{C26–C28 (capacity)},\ \text{C29 (stops)},\ \text{C30–C31 (cost)},\ \text{C32–C36 (main extensions)},\ \text{C37–C38 (domains)}.
\end{aligned}
$$

**Decomposition by day:** because of C2 and C17, no variable or constraint links different days except C21, which is redundant with the supplied shifts. The model can therefore be solved day by day.

---

## 6. Formulation B – tour-selection model (solved in Gurobi)

This is the Module 3 camera set-cover model with "= 1" instead of "≥ 1": tours play the role of camera positions, and shipments play the role of the locations to cover.

**Sets.** $R_{kd}$ = candidate tours of tractor $k$ on day $d$. Each $r\in R_{kd}$ is a fixed event sequence whose schedule satisfies **C6–C36** for that single tractor and day. $R=\bigcup_{k,d}R_{kd}$.

**Parameters.** $a_{ir}=1$ if tour $r$ carries shipment $i$; $c_r = H_r+E_r$ (C30–C31 evaluated for that route); $l_i$ = LTL cost.

**Decision variables**

| # | Variable | Type | Meaning | Source |
|---|---|---|---|---|
| **B-V1** | $\lambda_r$ | binary | 1 if candidate tour $r$ is selected | [B p.4]: "whether a tour is selected" |
| **B-V2** | $z_i$ | binary | 1 if shipment $i$ uses LTL | [B p.4]: "whether a shipment uses LTL" |

**Objective**
$$\min\ \sum_{r\in R}c_r\lambda_r+\sum_{i\in S}l_i z_i$$

**Constraints**

| ID | Constraint | Rule | Source |
|---|---|---|---|
| **B1** | $\displaystyle\sum_{r\in R}a_{ir}\lambda_r+z_i=1\quad\forall i\in S$ | Served exactly once | [B p.2] |
| **B2** | $\displaystyle\sum_{r\in R_{kd}}\lambda_r\le1\quad\forall k\in K,d\in D$ | One tour per tractor per day | [R p.3]; [B p.5] |
| **B3** | $\lambda_r, z_i\in\{0,1\}$ | Binary yes/no choices | [B p.4] |
| **B4** | $r\in R_{kd}$ only if the route passes C6–C36 (checked in Python when the candidate is generated) | All routing, timing, capacity, HOS, LIFO and compatibility rules | [B p.2]: "every route can actually be driven" |

The timing, load and break variables of Formulation A (V5–V16) become **outputs** of each candidate's schedule. They are exported in the plan ([B p.4, *Deliverables 2*]: "the shipments on each tour, their order, the tractor, the times, and the cost").

**Optimality claim.** If $R$ contains **every** feasible tour (possible on the simplified file, with at most 3 shipments per tour), a zero gap is a global optimum. Otherwise it is optimal *over the given tours*, and the report must say how the candidates were limited ([B p.5]: "Call it a global optimum only if every feasible tour was included… On the main file, say how the candidate tours were limited").

---

## 7. Simplified vs main: which constraints are active

| Constraint group | Simplified | Main | Source |
|---|---|---|---|
| C1–C21, C26–C31 | ✅ ($\bar D=8,\ \bar U=12,\ \bar r=12,\ \bar n=6$) | ✅ ($\bar D=11,\ \bar U=14,\ \bar r=10,\ \bar n=8$) | [B p.2 table]; [R p.3–4] |
| Facility hours | identical at all facilities | facility-specific | [B p.3]: "Facility hours are the same at every facility" |
| Equipment | one type (DRY 45,000 lb / 53 ft / 26 pallets) | DRY + REEFER (42,000 / 48 / 24) | [R p.3] |
| C22–C25 break | inactive ($\beta\equiv0$). With $\bar D=8\le\bar G$, the counter can never exceed 8 | ✅ | [B p.2 table]: "not required by the 8 h drive cap" |
| C32 chilled | vacuous (all ambient) | ✅ | [B p.3]; [R p.1] |
| C35 LIFO | inactive ($\lambda^{LIFO}=0$, side access: crossings allowed) | ✅ | [B p.2]; [R p.4] |
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
| p.1 Context | Three options: LTL, dedicated TL, consolidation | V1, V2, V3, V4 |
| p.1 Context | "each shipment stays on that trailer from its pickup until its delivery" | C8 |
| p.1 Context | Extra stops, detours, waiting, space or hours can make a combination infeasible or more expensive than LTL | C10–C31 + objective |
| p.1 Why it is hard | "A shipment uses weight, floor length, and pallet positions at the same time" | V10–V12, C26–C27 |
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
| p.2 | Loading order: strict LIFO only when lifo_required = 1; side access otherwise | V16, C35 |
| p.2 | "a shipment must be picked up before it is delivered" | C15 |
| p.2 | Static list; no holding | §8 |
| p.2 Problem statement | Choose dedicated / shared / LTL; minimize total cost; every route drivable | V1–V4; objective; B4 |
| p.2 Assignment | "Every shipment is served exactly once" | C1 / B1 |
| p.2 Routing | Stop sequence starting and ending at the home depot | V5, C6, C7, C9 |
| p.2 Timing | Leave depot, arrive at each stop, return | V6, V7, V8, V9 |
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
| p.2 | Consolidated = at least two shipments | V4, C5 |
| p.2 | Events ≤ max_stops (6 / 8; ≤ 3 / 4 shipments) | C29 |
| p.3 Objective | Linehaul + stop-off + LTL | Objective, V17, V18 |
| p.3 | Cost uses the assigned tractor's equipment, every mile from departure to return | C30 |
| p.3 | "An unused tractor costs nothing" | C30–C31 with $w=0$; C4 |
| p.3 | "LTL does not use a listed tractor" | C1 (z has no tractor index) |
| p.3 | Baseline all-LTL; report savings | Reporting (§4) |
| p.3 Data provided | Horizon ends 00:00 Day 4 / Day 8 | C18 |
| p.3 | Simplified: same facility hours, one equipment type, compatible freight, side access | §7 |
| p.3 | Main: refrigerated, compatibility, strict loading order, driver break | C32, C36, C35, C22–C25 |
| p.4 | drive_hours stored; use it as given | parameter $t_{uv}$ |
| p.4 | Absolute time = 24·day + hour; hours and shifts repeat daily | §2.5 |
| p.4 | LTL has enough capacity; no empty trailer moves | §8 |
| p.4 Deliverables 1 | Binaries for LTL choice and tour selection | V1, V3 / B-V1, B-V2 |
| p.4 Deliverables 2 | Output: shipments per tour, order, tractor, times, cost | V2, V5, V6–V9, V17–V18 |
| p.4–5 Grading | Simplified rules list: 3 capacities, windows, facility and depot hours, shifts, 8 h drive, 12 h duty, side access, one equipment type, one tour per tractor per day | C26–C27, C13–C14, C16–C17, C19, C20, §7, C6 / B2 |
| p.5 Grading | Main extensions: refrigerated, compatibility, strict LIFO, different opening hours, driver break | C32, C36, C35, C13–C14, C22–C25 |
| p.5 Gurobi | Global optimum only if every feasible tour was included | §6 |
| p.5 Results | Count LTL / dedicated / consolidated | V1, V4 |
| p.5 Results | Same-day delivery; no extra calendar day | C2, C17 |

### 9.2 Data README [R]

| Page / section | Statement in the file (abridged) | Model element |
|---|---|---|
| p.1 §1 | Each shipment **and** each TL tour finishes on its stated day | C2, C17 |
| p.1 §1 | Capacities and service windows are hard constraints | C14, C27 (no slack variables) |
| p.1 §1 | All-LTL baseline is feasible | §4 |
| p.1 §2 | Absolute time; days repeat; Day 0 planning | §2.5 |
| p.1 §2 | Joins: origin/destination → facilities; tractor → depot, equipment, tl_costs | §2.1, §2.3 |
| p.1 shipments | Indivisible shipment | C1, V2 binary |
| p.1 shipments | `day`: both pickup and delivery on that day | C2 |
| p.1 shipments | Window open = earliest service start; close = latest service completion; full handling must fit | C13, C14 |
| p.2 shipments | Weight, length, pallets: separate, additive, after each event; no stacking | C26–C27, §8 |
| p.2 shipments | Freight class only for LTL | $l_i$ (§2.5) |
| p.2 shipments | Chilled needs chilled-capable equipment | C32 |
| p.2 shipments | Identical group **and** temperature on board together; different groups may share sequentially | C36 (and m = 1, 2 allowed) |
| p.2 shipments | Service hours 0.5; handling is duty, not a break | C12, C20, C25 |
| p.2 facilities | Intersection of facility hours and window | C13, C14 |
| p.2 facilities | Service may end exactly at closing | C14 ($\le$) |
| p.2 facilities | Early trucks may wait; waiting is duty | C11, C20 |
| p.2 facilities | No queue or dock capacity | §8 |
| p.2 distance | Directed; same location = 0; no reverse symmetry | $\text{dist}_{uv}, t_{uv}$ |
| p.2 distance | Include depot approach and return in miles and driving time | C19, C30 |
| p.2 ltl_rates | Bands inclusive / exclusive; one match per shipment | $l_i$; input validation |
| p.2 ltl_rates | $l_i=\text{base}(1+\text{fuel})$ | $l_i$; objective |
| p.3 ltl_rates | No TL stop fees on LTL; no tractor for LTL | Objective; C1 |
| p.3 tl_costs | $c=\max\{\min,\ M\times\text{rate}\}+\max\{0,N-2\}\times\text{fee}$ | C30, C31 |
| p.3 tl_costs | All miles charged incl. empty legs; every pickup and delivery is an event even at a shared address; depot not an event | C30; $N^{kd}$ (§3.8) |
| p.3 tl_costs | No hourly or fuel cost for TL; unused tractors cost zero | Objective; C30–C31 |
| p.3 equipment | Capacities per type; chilled flag; both carry ambient; use the **assigned** type | C27, C32 |
| p.3 tractors | Driver–tractor–equipment fixed | $q(k)$, $h_k$ parameters |
| p.3 tractors | At most one complete tour per pair per day; several sequential pickups and deliveries allowed | C6 / B2; V5 |
| p.3 tractors | No freight remains when returning home | C28 |
| p.3 depots | Depots are bases only; parking sufficient | C9; §8 |
| p.3 depots | Tour fits depot hours and shift | C16, C17 |
| p.3 driver_rules | max_drive: includes approach, between stops, return | C19 |
| p.3 driver_rules | max_duty: departure to return, incl. handling, waiting, breaks | C20 |
| p.3 driver_rules | break_after_drive 8 h since last break; break before exceeding | C22–C24 |
| p.3 driver_rules | Break 0.5 h uninterrupted, at a stopped location; resets the within-tour counter; allowed after the last delivery | V13–V15, C11, C12, C22, C25 |
| p.3 driver_rules | Breaks do not reset daily driving or duty | C19, C20 |
| p.3–4 driver_rules | Handling cannot be a break; waiting only if recorded as off-duty rest | C12 (break after handling), C11 + V13 (recorded pre-service break) |
| p.4 driver_rules | min_rest provided by shifts; recheck if shifts change | C21 |
| p.4 driver_rules | No overnight reset; return within the daily shift | C17 |
| p.4 driver_rules | No lane exceeds the 8 h threshold | justifies breaks only at nodes |
| p.4 parameters | demand_days, horizon_end_day | $D$, C18 |
| p.4 parameters | time_step 0.25, speed 50 mph | already inside $t_{uv}$ |
| p.4 parameters | max_stops counts service events over the whole tour | C29 |
| p.4 parameters | lifo_required: 0 side access / 1 strict LIFO without rehandling | C35 |
| p.4 parameters | No trailer inventory sheet | §8 |
| p.5 §4 | Worked example: capacity sum after each event, windows, facility hours, 7 h drive / 9 h duty, 4 events ≤ 6, cost CAD 1,000, LTL CAD 2,742.75 | Unit test for C13–C31 and $l_i$ |
| p.5 §4 | "Using T01 here can affect which other shipments it can serve that day; assess the whole assignment" | C6 / B2 (tractor-day is a shared resource) |
| p.6 §5.1 | LIFO example: S001 then S002 → deliver S002 first | C35 (configuration 3 allowed, 5 forbidden) |
| p.6 §5.2 | S006 chilled/industrial needs REEFER; cannot be on board with S001 | C32, C36 |
| p.6 §5.2 | Reefer's smaller capacities must be checked | C27 via $q(k)$ |
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
5. **Worked example check** ([R p.5]): route D01→F01→F02→F04→F03→D01. Loads after each event are 22,000 → 31,000 → 22,000 → 0 lb, all ≤ 45,000 (C27). Drive 7 ≤ 8 (C19). Duty 16:00 − 07:00 = 9 ≤ 12 (C20). Events 4 ≤ 6 (C29). $H=\max(900, 350\times2.20)=900$, $E=50(4-2)=100$, cost CAD 1,000 (C30–C31). LIFO: configuration 3 (S002 nested in S001), allowed under C35.
