# MGSC 404 Group Project

We are building a shipment consolidation model for Uber Freight. Each shipment can travel on one of our trucks or through an outside less-than-truckload (LTL) carrier. Python and Gurobi choose a transportation plan that minimizes total cost.

## Goals

- Serve every shipment exactly once.
- Respect truck capacity, pickup and delivery windows, driver limits, equipment requirements, and loading rules.
- Compare the plan with the cost of sending everything through LTL.
- Independently check the routes and costs, and explain the results for our presentation.

## Python submission files

- [Gurobi_Main_V1.py](Gurobi_Main_V1.py) generates the main plan and calls the checker.
- [checker-full.py](checker-full.py) independently verifies the plan's routes and costs.

## Run locally

Install dependencies with `python -m pip install -r requirements.txt` and configure a Gurobi license. Run `python Gurobi_Main_V1.py` to solve the [main input workbook](data/project1_main.xlsx), export the plan, and check it. Run `python checker-full.py` to verify the [saved main plan](output/plan_main.xlsx) without rerunning the solver.

The [model walkthrough](main_model_explained.md), [formulation reference](Variables_Constraints.md), and [working notes](chatmath.md) are supporting material. Earlier models, exports, and report drafts are in the [archive](archive/README.md).
