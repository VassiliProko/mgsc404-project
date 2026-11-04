# MGSC 404 Group Project

Shipment consolidation: choose truck tours and LTL shipments to minimize transportation cost.

## Current simplified models

- `report_two_models.md`: explanation and comparison of the two approaches.
- `Gurobi_Simplified_v4.py`: candidate-tour generation and Gurobi selection.
- `full_gurobi_model_v1.py`: direct Gurobi routing formulation.
- `Variables_Constraints.md`: formulation reference.
- `data/`: simplified and main Excel inputs.
- `output/`: plans produced by the simplified models.

## Setup

Install Python dependencies with `python -m pip install -r requirements.txt` and configure a Gurobi license suitable for the model size. Run scripts from this repository, for example `python Gurobi_Simplified_v4.py`.

## Vassili's explorations

The notebook, checked results, comparison runs, and notebook instructions are in [`vassili-explorations/`](vassili-explorations/README.md).

To open the notebook: `python -m jupyter lab vassili-explorations/optimization-vassili.ipynb`.

## Independent plan checker

[`checker-full.py`](checker-full.py) independently checks
input data, routes, loading, time windows, driver rules, coverage and recalculated
costs. It reads the original workbook and exported plan without using Gurobi or
importing the model.

From this repository, check the main plan:

```bash
python checker-full.py
```

Or check a simplified plan:

```bash
python checker-full.py --data data/project1_simplified.xlsx --plan output/plan_simplified.xlsx
```

It prints `PASS` with recalculated savings, or stops at the first error with `FAIL`.
A pass checks feasibility and costs, not optimality. The Excel files are unchanged.
It builds on `checker-simplified.py`, retaining its sheet loading and coverage checks
and completing the route audit with full-data constraints.
