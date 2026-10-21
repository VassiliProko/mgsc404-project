# MGSC 404 Group Project

Starter environment for the shipment consolidation group project using Python, Jupyter, and Gurobi.

## Setup

Use Python 3. From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m jupyter lab project_starter.ipynb
```

On Windows, activate the environment with `.venv\Scripts\activate`.

Select the environment's Python kernel and run the notebook from top to bottom. The Gurobi setup check should return an objective of `1.0`.

Gurobi may provide a restricted license for small models. For the full project, configure an appropriate Gurobi academic license separately. Do not commit license files or credentials.

## Files

- `project_starter.ipynb`: imports, Gurobi setup check, Excel preview, and comments for later model development.
- `project1_simplified.xlsx`: simplified project dataset.
- `project1_main.xlsx`: main project dataset.
- `requirements.txt`: Python dependencies.

The setup check and coding conventions follow the MGSC 404 Assignment 1 and Assignment 2 notebooks. Those assignments are not included here. The notebook is an environment starter; the project optimization model is still to be implemented.
