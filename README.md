# MGSC 404 Group Project

This project chooses dedicated tours, shared tours, or outside LTL service to minimize transportation charges. The [LaTeX report](report.tex) covers the simplified development workbook and the main evaluation workbook.

## Python submission files

| Workbook | Gurobi model | Independent checker |
| --- | --- | --- |
| Simplified | [Gurobi_Simplified_v4.py](Gurobi_Simplified_v4.py) | [checker-simplified.py](checker-simplified.py) |
| Main | [Gurobi_Main_V1.py](Gurobi_Main_V1.py) | [checker-full.py](checker-full.py) |

The standalone simplified checker verifies shipment coverage and tractor assignments. The full checker replays routes and recalculates costs for either workbook.

## Run both models

From the project folder, install the dependencies and configure a Gurobi license.

    python -m pip install -r requirements.txt
    python Gurobi_Simplified_v4.py
    python Gurobi_Main_V1.py

Each model reads its workbook from data/, prints the all-LTL, greedy-pair, and Gurobi costs, and exports a plan to output/. It then runs the appropriate checker. The greedy-pair comparison is also in the output workbook's summary.

## Check exported plans independently

    python checker-simplified.py
    python checker-full.py --data data/project1_simplified.xlsx --plan output/plan_simplified.xlsx
    python checker-full.py

Use the full checker commands for route feasibility and cost verification. They accept custom `--data` and `--plan` paths.

Compile report.tex with pdfLaTeX or upload it as the main document in Overleaf. The [model walkthrough](main_model_explained.md), [formulation reference](Variables_Constraints.md), and [archive](archive/README.md) provide supporting material.
