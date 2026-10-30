# MGSC 404 Group Project

Shipment consolidation using Python route generation, Gurobi route selection, and an independent plan checker.

## Setup

Use Python 3. From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m jupyter lab optimization-vassili.ipynb
```

On Windows, activate the environment with `.venv\Scripts\activate`.

Select the environment's Python kernel and run the notebook from top to bottom.

Gurobi may provide a restricted license for small models. For the full project, configure an appropriate Gurobi academic license separately. Do not commit license files or credentials.

## Files

- `optimization-vassili.ipynb`: complete chronological solution, with short explanations and saved results.
- `project1_simplified.xlsx`: simplified project dataset.
- `project1_main.xlsx`: main project dataset.
- `requirements.txt`: Python dependencies.

The notebook defaults to the simplified workbook. Change `DATA_FILE` in section 2 to `project1_main.xlsx` and restart/run all cells to solve the main workbook, including LIFO, refrigeration, compatibility, and driver-break rules.

Sections cover data loading, the all-LTL baseline, route scheduling, candidate generation, the binary Gurobi model, independent checks, and final savings. The model uses `addVars`, `quicksum`, `addConstr`, and `optimize`, as in class.

`PARTNER_LIMIT` and `EXPAND_LIMIT` in section 5 control the search breadth. The solver finds the best plan within the generated candidates when its gap reaches zero; this does not prove a global optimum across all possible routes.

Results are written under `results-vassili/<dataset>/`: `truck_routes.csv`, `schedule.csv`, `ltl_shipments.csv`, `plan.json`, and `summary.json`. Schedule hours are local to the stated day (7.5 means 07:30); the checker includes travel, handling, waiting, breaks, and overnight rest.
