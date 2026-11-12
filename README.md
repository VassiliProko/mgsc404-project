# MGSC 404 Group Project

This project chooses dedicated tours, shared tours, or outside LTL service to minimize transportation charges. The [LaTeX report](report.tex) covers the simplified development workbook and the main evaluation workbook.

## Python submission files

| Workbook | Gurobi model | Independent checker |
| --- | --- | --- |
| Simplified | [Gurobi_Simplified_v4.py](Gurobi_Simplified_v4.py) | [checker-simplified.py](checker-simplified.py) |
| Main | [Gurobi_Main_V1.py](Gurobi_Main_V1.py) | [checker-full.py](checker-full.py) |

The checker entry points share the workbook-driven verification logic in checker-full.py. The simplified entry point supplies simplified workbook defaults and the three required rejection tests.

## Run both models

From the project folder, install the dependencies and configure a Gurobi license.

    python -m pip install -r requirements.txt
    python Gurobi_Simplified_v4.py
    python Gurobi_Main_V1.py

Each model reads its workbook from data/, prints the all-LTL, greedy-pair, and Gurobi costs, and exports a plan to output/. It then runs the appropriate checker. The greedy-pair comparison is also in the output workbook's summary.

## Check exported plans independently

    python checker-simplified.py --self-test
    python checker-full.py

The simplified command checks the valid plan and demonstrates that temporary test copies with an overload, a missed pickup window, and a double-booked tractor are rejected. Both checker commands accept custom --data and --plan paths.

Compile report.tex with pdfLaTeX or upload it as the main document in Overleaf. The [model walkthrough](main_model_explained.md), [formulation reference](Variables_Constraints.md), and [archive](archive/README.md) provide supporting material.
