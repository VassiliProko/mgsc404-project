# Comparing Two Gurobi Models

In the simplified file we have 60 shipments over 3 days and 8 trucks, split between two depots. Each shipment can either be sent with an outside LTL carrier or put on one of our own trucks. A truck can carry up to 3 shipments in a day, but it has to respect the pickup and delivery windows, stay under the weight, floor length and pallet limits, drive at most 8 hours, work at most 12 hours, and be back at the depot before the shift ends. Our goal is to find the cheapest mix of truck tours and LTL. If we sent everything by LTL it would cost CAD 93,298.35, so that is our baseline.

We solved the problem in two different ways, and each way has its own file.

## File 1: Gurobi_Simplified_v4.py

This file splits the work between Python and Gurobi. First, Python reads the Excel file and builds a list of every truck tour that could work. For each day, each truck and each group of one, two or three shipments, it tries every possible order of stops and simulates the trip stop by stop. If a tour misses a time window, goes over capacity, or uses too many driving or duty hours, it gets thrown out. Python also picks the best time to leave the depot so the driver doesn't waste hours waiting. In the end we get 1,592 tours that are allowed.

Then Gurobi takes this list and chooses the cheapest combination. Every shipment has to be covered exactly once, either by a tour or by LTL, and each truck can only do one tour per day. Because Gurobi only has to say yes or no to about 1,600 options, it solves the whole thing in less than a second.

## File 2: full_gurobi_model_v1.py

This file gives everything to Gurobi. There is no list of tours. Gurobi has to decide at the same time which shipments go on which truck, which road the truck takes between stops, what time it arrives at each place and how full it is after each stop. All of the constraints from our formulation are written directly into Gurobi, using the Big-M method from class for the timing and the loads. To help it run fatser, we removed roads that could never be driven in time, and we made the identical trucks at each depot get used in a fixed order.

The problem is that this model is very big. Each day has around 10,000 yes/no variables and 67,000 constraints. Gurobi can find good plans fairly quickly, but proving that nothing cheaper exists takes a very long time, because the Big-M constraints give it weak lower bounds.

## Results

| Run | Total cost (CAD) | Savings vs all-LTL | Gap | Time |
|---|---|---|---|---|
| File 1 (tour selection) | 36,246.15 | 61.15% | 0% (optimal) | about 2 seconds |
| File 2 | 36,246.15 | 61.15% | 30.8% | about 9 hours |

File 1 found a total cost of CAD 36,246.15, which saves CAD 57,052.20 (61.15%) compared with sending everything by LTL. The plan uses all 8 trucks on all 3 days, for 24 tours: 20 of them carry two shipments and 4 carry three. Only 8 shipments stay on LTL, mostly the ones that don't fit well with others because of their time windows or locations. There are no single-shipment truck tours, because a truck always costs at least the CAD 900 minimum, which was never cheaper than LTL. Because Python listed every possible tour and Gurobi closed the gap to 0%, we know this answer is the global optimum.

File 2 needed much more time. In 9 hours (after hitting the limit), it reached the same cost of CAD 36,246.15, but it still could not prove that this was the best, and the gap was still 30.8%. Its plan is slightly different from File 1's because one shipment is swapped between a truck and LTL, but the cost is exactly the same, since several plans tie for the best cost.

We re-checked both plans against the original Excel data. Every shipment is served once, no truck is used twice on the same day, no rule is broken, and the costs add up correctly.
