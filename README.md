# MGSC 404 Group Project

We are building a shipment consolidation model for Uber Freight. Each shipment can travel on one of our trucks or through an outside less-than-truckload (LTL) carrier. Python and Gurobi choose a transportation plan that minimizes total cost.

## Goals

- Serve every shipment exactly once.
- Respect truck capacity, pickup and delivery windows, driver limits, equipment requirements, and loading rules.
- Compare the plan with the cost of sending everything through LTL.
- Independently check the routes and costs, and explain the results for our presentation.

## Files

- [Gurobi_Main_V1.py](Gurobi_Main_V1.py): model for the main dataset.
- [Gurobi_Simplified_v4.py](Gurobi_Simplified_v4.py): model for the simplified dataset.
- [checker-full.py](checker-full.py): independent route, constraint, and cost checker.
- [checker-simplified.py](checker-simplified.py): earlier checker for the simplified dataset.
- [full_gurobi_model_v1.py](full_gurobi_model_v1.py): alternative direct routing model for the simplified dataset.
- [Variables_Constraints.md](Variables_Constraints.md): mathematical formulation reference.
- [report_two_models.md](report_two_models.md): simplified-model comparison and results.
- [data/](data/): input workbooks. [output/](output/): exported plans.

Install dependencies with `python -m pip install -r requirements.txt` and configure a Gurobi license. Run scripts from this folder. Use `python checker-full.py` to check the saved main plan.

Earlier model versions and results are in [archive/](archive/README.md).
