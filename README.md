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
