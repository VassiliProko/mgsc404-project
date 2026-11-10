# How the Main File Model Works

## The problem

The main file is a lot bigger than the simplified one. We have 800 shipments over 7 days (about 115 a day), 24 trucks at 4 depots, and 24 facilities. Each shipment can either go with an outside LTL carrier or ride on one of our trucks. Our goal is to find the cheapest mix. Sending everything by LTL would cost CAD 1,557,535.85, so that is the number we try to beat.

The main file also has more rules than the simplified one. Chilled freight can only go on a reefer truck, and we only have 4 of those. Shipments can only be on the truck together if they have the same temperature and the same group (for example, food can't ride with industrial freight). Unloading is strict last-in, first-out, so the last shipment loaded has to come off first. Each facility has its own opening hours. Drivers can drive up to 11 hours and work up to 14, but they need a 30-minute break before driving more than 8 hours since their last break. A truck can make at most 8 stops, which means at most 4 shipments.

## Step 1: Python builds the list of possible tours

First, Python reads the Excel file and works out, for every day and every type of truck, which groups of shipments could share a truck. Trucks with the same depot, truck type and shift are basically identical, so we only test each "type" once (there are 9 types) and copy the tours to the matching trucks.

For each group of shipments, the code tries every stop order the loading rule allows and simulates the trip stop by stop. It checks the time windows, the facility and depot hours, the shift, the capacity after every stop, the reefer and compatibility rules, LIFO, and the driving and duty limits. If any rule is broken, the tour is thrown out. The code also chooses the departure time and where to put the driver's break. It first tries the simple choice (break at the last stop before 8 hours of driving), and if that doesn't work it tries every possible break spot and departure time.

To save time, the code only tries a group of 3 shipments if every pair inside it already works, and a group of 4 only if every group of 3 inside it works. We tested this shortcut by trying every group of 3 and 4 directly, and it didn't lose a single tour. In the end there were 12,533 tours that follow all the rules.

## Step 2: Gurobi picks the cheapest combination

Then Gurobi takes this list and picks the best tours, the same way as the camera covering problem from class. It has two kinds of yes/no decisions: which tours to use and which shipments go LTL. Every shipment has to be covered exactly once, by a tour or by LTL, and each truck can only do one tour per day. The goal is to minimize the total cost of the tours plus the LTL. Because Gurobi only has to choose from a ready-made list, it solves the whole thing in less than a second.

## Step 3: Results are saved

The code prints a report (Gurobi version, status, gap, cost, savings) and saves the full plan to `plan_main.xlsx`. The file has every tour with its stops, times, breaks and loads, so the checker can go through it again.

## Results

| | |
|---|---|
| All-LTL cost | CAD 1,557,535.85 |
| Our cost | CAD 1,137,319.80 |
| Savings | CAD 420,216.05 (26.98%) |
| Gurobi status | OPTIMAL, gap 0% |
| Tours used | 168 (all 24 trucks, every day) |
| Shipments still on LTL | 489 of 800 |

All 24 trucks are used on all 7 days: 38 tours carry one shipment, 117 carry two and 13 carry three. No tour carries four shipments, because with LIFO, the compatibility rule and the tighter windows, none of them were possible. About 111 of the tours needed a driver break.

The savings are smaller than on the simplified file (27% instead of 61%) because the main file's rules make it much harder to combine shipments. Many shipments can't be reached in time from the depots at all, chilled freight has to share only 4 reefers, and LIFO and compatibility rule out a lot of pairs. So a lot of shipments just end up staying on LTL.

Since Gurobi finished with status OPTIMAL and a 0% gap, our plan is the best combination of the tours on the list. And since our tests showed the shortcuts didn't leave any feasible tour out, we're confident it is the best plan overall. We also ran the plan through a separate checker, and it found no broken rules and got the same cost.