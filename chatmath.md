# MGSC 404 — Project 1: Mathematical Formulation

## 1. Objective Function — Minimize Total Transportation Cost

\[
\boxed{
\min Z =
\sum_{k\in K} C_k^{TL}y_k
+
\sum_{i\in I} C_i^{LTL}z_i
}
\]

Where the truckload cost is:

\[
C_k^{TL}
=
\max\left\{
C_{e(k)}^{\min},
r_{e(k)}D_k
\right\}
+
c_{e(k)}^{stop}\max(0,N_k-2)
\]

The LTL cost for shipment \(i\) is:

\[
C_i^{LTL}
=
B_i(1+f_i)
\]

Where:
- \(C_k^{TL}\): total cost of TL tour \(k\).
- \(C_i^{LTL}\): LTL cost of shipment \(i\).
- \(D_k\): total miles traveled on tour \(k\), including depot departure and return.
- \(N_k\): number of pickup and delivery events.
- \(B_i\): base LTL rate.
- \(f_i\): LTL fuel surcharge.

## 2. Sets and Parameters

- \(I\): set of shipments.
- \(K\): set of candidate TL tours.
- \(T\): set of tractors.
- \(D\): set of operating days.
- \(w_i\): weight of shipment \(i\).
- \(l_i\): floor length of shipment \(i\).
- \(p_i\): pallet requirement of shipment \(i\).
- \(W_k\): weight capacity of the equipment assigned to tour \(k\).
- \(L_k\): floor-length capacity of the equipment assigned to tour \(k\).
- \(P_k\): pallet capacity of the equipment assigned to tour \(k\).
- \(S^{max}\): maximum number of service events per tour.
- \(h_i^P\): pickup service duration for shipment \(i\).
- \(h_i^D\): delivery service duration for shipment \(i\).
- \(d_{jm}\): driving time between consecutive locations \(j\) and \(m\).
- \(C_k^{TL}\): cost of TL tour \(k\).
- \(C_i^{LTL}\): cost of sending shipment \(i\) by LTL.

## 3. Decision Variables

Shipment assignment:

\[
x_{ik}=
\begin{cases}
1 & \text{if shipment }i\text{ is assigned to tour }k\\
0 & \text{otherwise}
\end{cases}
\]

LTL assignment:

\[
z_i=
\begin{cases}
1 & \text{if shipment }i\text{ is shipped by LTL}\\
0 & \text{otherwise}
\end{cases}
\]

Tour selection:

\[
y_k=
\begin{cases}
1 & \text{if tour }k\text{ is selected}\\
0 & \text{otherwise}
\end{cases}
\]

## 4. Assignment Constraints

### 4.1 Every shipment is served exactly once

\[
\boxed{
\sum_{k\in K}x_{ik}+z_i=1
\qquad \forall i\in I
}
\]

Each shipment must be assigned to exactly one TL tour or sent by LTL.

### 4.2 A tour must be selected if it carries a shipment

\[
\boxed{
x_{ik}\le y_k
\qquad \forall i\in I,\ k\in K
}
\]

### 4.3 Dedicated and consolidated tours

For a dedicated tour:

\[
\boxed{
\sum_{i\in I}x_{ik}=1
}
\]

For a consolidated tour:

\[
\boxed{
2\le\sum_{i\in I}x_{ik}
\le \frac{S^{max}}{2}
}
\]

These are tour classifications. If the objective only minimizes cost, they do not need separate binary variables unless the implementation requires explicit reporting of dedicated and consolidated tours.

## 5. Capacity Constraints

All three capacity dimensions must be satisfied.

### 5.1 Weight capacity

\[
\boxed{
\sum_{i\in I}w_ix_{ik}
\le W_k
\qquad \forall k\in K
}
\]

### 5.2 Floor-length capacity

\[
\boxed{
\sum_{i\in I}l_ix_{ik}
\le L_k
\qquad \forall k\in K
}
\]

### 5.3 Pallet capacity

\[
\boxed{
\sum_{i\in I}p_ix_{ik}
\le P_k
\qquad \forall k\in K
}
\]

### Equipment capacities

| Equipment | Weight | Floor length | Pallets |
|---|---:|---:|---:|
| DRY | 45,000 lb | 53 ft | 26 |
| REEFER | 42,000 lb | 48 ft | 24 |

The capacity values must be taken from the assigned equipment type.

## 6. Maximum Stops

Every shipment generates one pickup and one delivery.

\[
N_k=2\sum_{i\in I}x_{ik}
\]

Therefore:

\[
\boxed{
2\sum_{i\in I}x_{ik}
\le S^{max}
\qquad \forall k\in K
}
\]

Simplified instance:

\[
\boxed{
S^{max}=6
\quad\Rightarrow\quad
\sum_i x_{ik}\le3
}
\]

Main instance:

\[
\boxed{
S^{max}=8
\quad\Rightarrow\quad
\sum_i x_{ik}\le4
}
\]

## 7. Pickup and Delivery Time Windows

Let \(T_i^P\) be the pickup start time and \(T_i^D\) be the delivery start time.

### 7.1 Pickup window

\[
\boxed{
P_i^{open}\le T_i^P
}
\]

\[
\boxed{
T_i^P+h_i^P\le P_i^{close}
}
\]

### 7.2 Delivery window

\[
\boxed{
D_i^{open}\le T_i^D
}
\]

\[
\boxed{
T_i^D+h_i^D\le D_i^{close}
}
\]

The entire handling duration must fit inside the relevant time window.

### 7.3 Pickup before delivery

\[
\boxed{
T_i^P+h_i^P\le T_i^D
}
\]

This constraint ensures that the pickup is completed before delivery begins.

## 8. Facility Operating Hours

For shipment \(i\), let \(o(i)\) denote its origin and \(d(i)\) its destination.

### 8.1 Pickup facility

\[
\boxed{
F_{o(i)}^{open}\le T_i^P
}
\]

\[
\boxed{
T_i^P+h_i^P\le F_{o(i)}^{close}
}
\]

### 8.2 Delivery facility

\[
\boxed{
F_{d(i)}^{open}\le T_i^D
}
\]

\[
\boxed{
T_i^D+h_i^D\le F_{d(i)}^{close}
}
\]

Both the shipment time windows and facility operating hours must be satisfied.

## 9. Route Timing

If location \(m\) is visited immediately after location \(j\), the next service cannot begin before travel and handling at the previous stop are completed.

\[
\boxed{
T_m\ge T_j+h_j+d_{jm}
}
\]

Where:
- \(T_j\): service start at location \(j\).
- \(h_j\): handling duration at location \(j\).
- \(d_{jm}\): stored driving time from \(j\) to \(m\).

Use the provided `drive_hours` values.

## 10. Depot and Driver Shift Constraints

Every tour must begin and end at the tractor's assigned home depot.

\[
\boxed{
Location_k^{start}=Depot_k
}
\]

\[
\boxed{
Location_k^{end}=Depot_k
}
\]

The departure and return times must respect the depot operating hours:

\[
\boxed{
Depot_k^{open}\le T_k^{departure}
}
\]

\[
\boxed{
T_k^{return}\le Depot_k^{close}
}
\]

The tour must also fit inside the driver's shift:

\[
\boxed{
ShiftStart_k\le T_k^{departure}
}
\]

\[
\boxed{
T_k^{return}\le ShiftEnd_k
}
\]

## 11. Maximum Driving Time

Let \(q_{jmk}=1\) if tour \(k\) travels directly from location \(j\) to location \(m\).

\[
\boxed{
\sum_{j,m}d_{jm}q_{jmk}
\le H_k^{drive}
}
\]

Simplified instance:

\[
\boxed{
H_k^{drive}=8\text{ hours}
}
\]

Main instance:

\[
\boxed{
H_k^{drive}=11\text{ hours}
}
\]

The total includes the outbound depot leg, all intermediate legs, and the return leg.

## 12. Maximum Duty Time

Duty time is the elapsed time from departure until return.

\[
\boxed{
T_k^{return}-T_k^{departure}
\le H_k^{duty}
}
\]

Simplified instance:

\[
\boxed{
H_k^{duty}=12\text{ hours}
}
\]

Main instance:

\[
\boxed{
H_k^{duty}=14\text{ hours}
}
\]

Duty includes driving, handling, waiting, and breaks.

## 13. One Tour per Tractor per Day

Let \(K(t,d)\) be the set of candidate tours assigned to tractor \(t\) on day \(d\).

\[
\boxed{
\sum_{k\in K(t,d)}y_k\le1
\qquad \forall t\in T,\ d\in D
}
\]

Each tractor can perform at most one complete tour per day.

## 14. Equipment and Temperature Compatibility

Let \(Chilled_i=1\) if shipment \(i\) requires chilled equipment, and let \(ChilledCapable_k=1\) if the equipment assigned to tour \(k\) supports chilled freight.

\[
\boxed{
x_{ik}\le ChilledCapable_k
\quad
\text{if }Chilled_i=1
}
\]

Shipments with different temperature requirements cannot share a tour:

\[
\boxed{
x_{ik}+x_{jk}\le1
\quad
\text{if }Temp_i\ne Temp_j
}
\]

## 15. Compatibility-Group Constraints

Shipments with different compatibility groups cannot share a tour.

\[
\boxed{
x_{ik}+x_{jk}\le1
\quad
\text{if }Group_i\ne Group_j
}
\]

The compatibility groups are `general`, `food`, and `industrial`.

Different groups may use the same tractor at different times after the previous shipment has been delivered.

## 16. LIFO Loading Order — Main Instance Only

When `lifo_required = 1`, the last shipment picked up must be the first shipment delivered.

For two shipments \(i\) and \(j\) on the same tour:

\[
\boxed{
T_i^P<T_j^P
\Rightarrow
T_j^D<T_i^D
}
\]

This is a logical implication. A direct MILP implementation requires sequencing variables or a candidate-tour formulation that only includes LIFO-feasible sequences.

Simplified instance:

\[
\boxed{
lifo\_required=0
}
\]

Main instance:

\[
\boxed{
lifo\_required=1
}
\]

## 17. Driver Break — Main Instance Only

The main instance requires a 0.5-hour qualifying break before accumulated driving since the last qualifying break would exceed 8 hours.

\[
\boxed{
DrivingSinceLastBreak\le8
}
\]

When a break is required:

\[
\boxed{
BreakDuration=0.5\text{ hours}
}
\]

A break resets the accumulated-driving counter since the last break, but it does not reset total tour driving or duty time. Handling cannot count as a break.

The simplified instance does not require this break rule.

## 18. Same-Day Completion and Horizon

Every shipment and complete TL tour must finish on its assigned day.

For a shipment assigned to day \(d_i\):

\[
\boxed{
24d_i\le T_i^P
}
\]

\[
\boxed{
T_i^D+h_i^D<24(d_i+1)
}
\]

The complete tour must also return before the end of that day.

The absolute-time convention is:

\[
\boxed{
AbsoluteTime=24(day)+hour
}
\]

Horizon limits:

- Simplified: \(T<96\), ending at 00:00 on Day 4.
- Main: \(T<192\), ending at 00:00 on Day 8.

## 19. No Freight Remaining at Return

Every shipment assigned to a tour must be delivered before the truck returns to its home depot.

\[
\boxed{
Load_k^{return}=0
\qquad \forall k\in K
}
\]

The load must also remain within all three equipment capacities after every pickup and delivery event.

## 20. Binary Variable Restrictions

\[
\boxed{
x_{ik}\in\{0,1\}
\qquad \forall i,k
}
\]

\[
\boxed{
y_k\in\{0,1\}
\qquad \forall k
}
\]

\[
\boxed{
z_i\in\{0,1\}
\qquad \forall i
}
\]

All yes/no assignment and selection decisions are binary.

---

## 21. Simplified vs. Main Instance

| Constraint | Simplified | Main |
|---|---:|---:|
| Maximum weight — DRY | 45,000 lb | 45,000 lb |
| Maximum weight — REEFER | Not applicable | 42,000 lb |
| Maximum floor length — DRY | 53 ft | 53 ft |
| Maximum floor length — REEFER | Not applicable | 48 ft |
| Maximum pallets — DRY | 26 | 26 |
| Maximum pallets — REEFER | Not applicable | 24 |
| Maximum service events | 6 | 8 |
| Maximum shipments per tour | 3 | 4 |
| Maximum driving | 8 h | 11 h |
| Maximum duty | 12 h | 14 h |
| Driver break | Not required | 0.5 h after 8 h accumulated driving |
| LIFO | No | Yes |
| Temperature compatibility | Compatible ambient freight | Required |
| Compatibility groups | One compatible group | Must match |
| Facility hours | Common | Vary |
| Planning horizon | Days 1–3 | Days 1–7 |

---

## 22. Final Optimization Model

The complete problem is:

\[
\boxed{
\begin{aligned}
\min \quad
& \sum_{k\in K}C_k^{TL}y_k
+\sum_{i\in I}C_i^{LTL}z_i\\[4pt]
\text{subject to}\quad
& \sum_{k\in K}x_{ik}+z_i=1
&&\forall i\in I\\
& x_{ik}\le y_k
&&\forall i\in I,\ k\in K\\
& \sum_iw_ix_{ik}\le W_k
&&\forall k\in K\\
& \sum_il_ix_{ik}\le L_k
&&\forall k\in K\\
& \sum_ip_ix_{ik}\le P_k
&&\forall k\in K\\
& 2\sum_ix_{ik}\le S^{max}
&&\forall k\in K\\
& \text{Pickup-before-delivery constraints}\\
& \text{Pickup and delivery time windows}\\
& \text{Facility and depot operating hours}\\
& \text{Route continuity and travel-time constraints}\\
& \text{Driver shift, driving and duty limits}\\
& \text{One tour per tractor per day}\\
& \text{Equipment and temperature compatibility}\\
& \text{Compatibility-group constraints}\\
& \text{LIFO constraints, for the main instance}\\
& \text{Driver break constraints, for the main instance}\\
& \text{Same-day completion and horizon constraints}\\
& x_{ik},y_k,z_i\in\{0,1\}
\end{aligned}
}
\]

**Modeling note:** This is the candidate-tour set-partitioning formulation. If every candidate tour has already been fully checked for capacity, timing, routing, LIFO, equipment, and driver feasibility, those conditions are enforced during tour generation rather than repeated in Gurobi. In that implementation, the essential Gurobi model is the cost minimization, assignment constraints, and binary tour-selection variables. The project requires Gurobi to select the plan and an independent checker to validate the exported solution.
