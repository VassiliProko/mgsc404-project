"""Independently check an exported truck and LTL plan against the original workbook.

Run with python checker-full.py, or supply --data and --plan for another case.
The checker replays truck stops and breaks, checks shipment coverage, and
recalculates the bill. Driver limits and prices come from the original data.
"""
import argparse
import math
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

# 1. Set defaults and define input columns

FOLDER = Path(__file__).resolve().parent
DATA_FILE = FOLDER / "data" / "project1_main.xlsx"
PLAN_FILE = FOLDER / "output" / "plan_main.xlsx"
EPS = 1e-6
CENT = 0.01 + EPS

# Required numeric and text columns, grouped by input sheet.
SCHEMA = {
    'shipments': ('day pickup_open_hour pickup_close_hour delivery_open_hour delivery_close_hour weight_lb linear_feet pallets freight_class pickup_service_hours delivery_service_hours', 'shipment_id origin_id destination_id temperature compatibility_group'),
    'facilities': ('open_hour close_hour', 'facility_id'),
    'depots': ('open_hour close_hour', 'depot_id'),
    'distance_matrix': ('distance_miles drive_hours', 'from_id to_id'),
    'ltl_rates': ('freight_class weight_from_lb weight_to_lb distance_from_miles distance_to_miles base_cost_cad fuel_surcharge_pct', ''),
    'tl_costs': ('linehaul_cad_per_mile minimum_linehaul_cad extra_stop_cad', 'equipment_id'),
    'equipment': ('weight_capacity_lb length_capacity_feet pallet_capacity chilled_capable', 'equipment_id'),
    'tractors': ('shift_start_hour shift_end_hour', 'tractor_id home_depot_id equipment_id'),
    'driver_rules': ('value', 'parameter'), 'parameters': ('value', 'parameter'),
}

# These helpers stop at the first error and include its location in the message.
def require(condition, message):
    if not condition:
        raise ValueError(message)


def numeric(value, label):
    require(pd.notna(value) and not isinstance(value, (str, bool)), f'{label}: expected a number')
    require(math.isfinite(float(value)), f'{label}: number must be finite')
    return float(value)


def whole_number(value, label, minimum=0):
    value = numeric(value, label)
    require(value.is_integer() and value >= minimum, f'{label}: expected a whole number >= {minimum}')
    return int(value)


def check_equal(actual, expected, label, tolerance=EPS):
    require(abs(numeric(actual, label) - expected) <= tolerance, f'{label}: reported {actual}, expected {expected:g}')


def columns(frame, needed, label):
    require(set(needed.split()) <= set(frame.columns), f'{label}: missing required columns')


def unique(frame, keys, label):
    require(not frame[keys].isna().any().any() and not frame.duplicated(keys).any(), f'{label}: blank or duplicate IDs')


# 2. Check workbook sheets

needed_sheets_data = [
    "shipments",
    "facilities",
    "distance_matrix",
    "ltl_rates",
    "tl_costs",
    "equipment",
    "tractors",
    "depots",
    "driver_rules",
    "parameters"
]

needed_sheets_plan = [
    "tours",
    "stops",
    "ltl"
]

def check_sheets(sheets, needed_sheets, path):
    """Checks the Excel sheets, raises errors if any expected sheets are missing."""
    missing = []
    for name in needed_sheets:
        if name not in sheets:
            missing.append(name)
    if missing:
        raise ValueError(f"{path} is missing sheets: {missing}")


# 3. Read the workbooks

def load_data(path):
    """Loads the original workbook, separately from the exported plan."""
    sheets = pd.read_excel(path, sheet_name=None)
    check_sheets(sheets, needed_sheets_data, path.name)
    return sheets

def load_plan(path):
    """Loads and reads the proposed plan from the model."""
    sheets = pd.read_excel(path, sheet_name=None)
    check_sheets(sheets, needed_sheets_plan, path.name)
    return sheets

# 4. Check shipment coverage

def ids_from_text(text):
    return [part.strip() for part in str(text).split(',') if part.strip()]


def check_coverage(data, plan):
    counts = Counter(dict.fromkeys(data['shipments']['shipment_id'].map(str), 0))
    used = set()
    for key in plan['ltl']['shipment_id'].map(str):
        require(key in counts, f'LTL list has unknown shipment {key}')
        counts[key] += 1
    for tour in plan['tours'].itertuples(index=False):
        day = int(tour.day)
        truck_day = (tour.tractor_id, day)
        require(truck_day not in used, f'Tractor {tour.tractor_id} is used twice on day {day}')
        used.add(truck_day)
        for key in ids_from_text(tour.shipments):
            require(key in counts, f'Tour {tour.tour_id} has unknown shipment {key}')
            counts[key] += 1
    for key, count in counts.items():
        require(count == 1, f'Shipment {key} is served {count} time(s); expected 1')
    return 'OK'


# 5. Validate the original data

def check_travel(travel, locations, parameters):
    # Travel is directed. Check every pair and the stored time rounded up to the workbook step.
    unique(travel, ['from_id', 'to_id'], 'distance_matrix')
    expected_pairs = {(a, b) for a in locations for b in locations if a != b}
    require(set(zip(travel.from_id, travel.to_id)) == expected_pairs,
            'Missing or unexpected travel pairs')
    lanes = {}
    for row in travel.itertuples(index=False):
        require(row.distance_miles > 0 and row.drive_hours > 0, 'Travel distance/time must be positive')
        step = parameters['time_step_hours']
        rounded = math.ceil(row.distance_miles / parameters['average_speed_mph'] / step - EPS) * step
        check_equal(row.drive_hours, rounded, f'Travel {row.from_id} -> {row.to_id}')
        lanes[row.from_id, row.to_id] = (row.distance_miles, row.drive_hours)
    return lanes


def price_ltl(rates, shipments, lanes):
    # Each shipment must match one LTL band: lower bounds count, upper bounds do not.
    ltl_costs = {}
    for row in rates.itertuples(index=False):
        whole_number(row.freight_class, 'LTL freight class', 1)
        require(0 <= row.weight_from_lb < row.weight_to_lb and 0 <= row.distance_from_miles < row.distance_to_miles, 'Invalid LTL band')
        require(row.base_cost_cad >= 0 and row.fuel_surcharge_pct >= 0, 'Negative LTL price')
    for key, row in shipments.items():
        distance = 0 if row['origin_id'] == row['destination_id'] else lanes[row['origin_id'], row['destination_id']][0]
        matches = rates[
            (rates.freight_class == row['freight_class'])
            & (rates.weight_from_lb <= row['weight_lb'])
            & (row['weight_lb'] < rates.weight_to_lb)
            & (rates.distance_from_miles <= distance)
            & (distance < rates.distance_to_miles)
        ]
        require(len(matches) == 1, f'{key}: expected one LTL rate, found {len(matches)}')
        rate = matches.iloc[0]
        ltl_costs[key] = rate.base_cost_cad * (1 + rate.fuel_surcharge_pct)
    return ltl_costs


def check_data(data):
    # Check sheet structure and cell types, then build lookup tables by ID.
    tables = {}
    for name, (numbers, text) in SCHEMA.items():
        require(name in data, f'Data: missing sheet {name}')
        frame = data[name]
        columns(frame, numbers + ' ' + text, name)
        for column in numbers.split():
            for value in frame[column]:
                numeric(value, f'{name}.{column}')
        for column in text.split():
            require(frame[column].map(lambda v: isinstance(v, str) and bool(v.strip())).all(), f'{name}.{column}: blank or invalid text')
        if name not in ('distance_matrix', 'ltl_rates'):
            key = text.split()[0]
            unique(frame, [key], name)
            tables[name] = frame.set_index(key).to_dict('index')
    shipments, tractors = tables['shipments'], tables['tractors']
    facilities, depots, equipment = tables['facilities'], tables['depots'], tables['equipment']
    rules = {key: row['value'] for key, row in tables['driver_rules'].items()}
    parameters = {key: row['value'] for key, row in tables['parameters'].items()}

    # Check operating hours, rule values and planning days.
    for key in 'max_drive_hours max_duty_hours min_rest_hours break_after_drive_hours break_hours'.split():
        require(key in rules and rules[key] > 0, f'Driver rule {key}: missing or not positive')
    for key in 'demand_days horizon_end_day time_step_hours average_speed_mph max_stops lifo_required'.split():
        require(key in parameters, f'Parameters: missing {key}')
    days = whole_number(parameters['demand_days'], 'demand_days', 1)
    horizon = whole_number(parameters['horizon_end_day'], 'horizon_end_day', 1)
    whole_number(parameters['max_stops'], 'max_stops', 2)
    require(horizon > days and parameters['time_step_hours'] > 0 and parameters['average_speed_mph'] > 0, 'Invalid horizon, speed or time step')
    require(parameters['lifo_required'] in (0, 1), 'lifo_required must be 0 or 1')
    require(not set(facilities) & set(depots), 'Facility and depot IDs must be distinct')
    for name, prefixes in [('facilities', ['']), ('depots', ['']), ('tractors', ['shift_']), ('shipments', ['pickup_', 'delivery_'])]:
        for key, row in tables[name].items():
            for prefix in prefixes:
                start = row[prefix + ('start_hour' if name == 'tractors' else 'open_hour')]
                end = row[prefix + ('end_hour' if name == 'tractors' else 'close_hour')]
                require(0 <= start < end <= 24, f'{key}: invalid {prefix}hours')

    # Check capacities, fixed truck assignments and shipment references.
    for key, row in equipment.items():
        require(all(row[c] > 0 for c in ['weight_capacity_lb', 'length_capacity_feet', 'pallet_capacity']), f'{key}: capacities must be positive')
        whole_number(row['pallet_capacity'], f'{key} pallet capacity', 1)
        require(row['chilled_capable'] in (0, 1), f'{key}: invalid chilled capability')
    for key, row in tables['tl_costs'].items():
        require(key in equipment and all(row[column] >= 0 for column in SCHEMA['tl_costs'][0].split()), f'{key}: unknown equipment or negative TL cost')
    for key, row in tractors.items():
        require(row['home_depot_id'] in depots and row['equipment_id'] in equipment and row['equipment_id'] in tables['tl_costs'], f'{key}: unknown depot, equipment or price')
    for key, row in shipments.items():
        require(row['origin_id'] in facilities and row['destination_id'] in facilities, f'{key}: unknown facility')
        require(1 <= whole_number(row['day'], f'{key} day', 1) <= days, f'{key}: day outside horizon')
        require(all(row[c] > 0 for c in ['weight_lb', 'linear_feet', 'pallets', 'pickup_service_hours', 'delivery_service_hours']), f'{key}: demands and durations must be positive')
        for field in ['pallets', 'freight_class']:
            whole_number(row[field], f'{key} {field}', 1)
        require(row['temperature'] in ('ambient', 'chilled') and row['compatibility_group'] in ('general', 'food', 'industrial'), f'{key}: invalid freight type')

    lanes = check_travel(data['distance_matrix'], set(facilities) | set(depots), parameters)
    ltl_costs = price_ltl(data['ltl_rates'], shipments, lanes)
    return tables, rules, parameters, lanes, ltl_costs


# 6. Check routes, driver rules and costs

LOAD_COLUMNS = ['load_weight_lb', 'load_feet', 'load_pallets']


def check_breaks(breaks, used, location, stop_times, minimum_hours, label):
    """Match recorded breaks to this service stop by location and time."""
    arrival, start, end, leave = stop_times
    intervals = []
    for idx, break_row in enumerate(breaks):
        if idx in used or break_row.location_id != location:
            continue
        b_start = numeric(break_row.service_start_abs, f'{label} break start')
        b_end = numeric(break_row.service_end_abs, f'{label} break end')
        if b_start < arrival - EPS or b_end > leave + EPS:
            continue
        require(b_end - b_start >= minimum_hours - EPS, f'{label}: break too short')
        require(b_end <= start + EPS or b_start >= end - EPS, f'{label}: break overlaps handling')
        require(pd.isna(break_row.shipment_id), f'{label}: break has shipment ID')
        for column in ['leg_miles', 'leg_drive_hours']:
            check_equal(getattr(break_row, column), 0, f'{label} break {column}')
        intervals.append((b_start, b_end))
        used.add(idx)
    intervals.sort()
    for (_, previous_end), (next_start, _) in zip(intervals, intervals[1:]):
        require(previous_end <= next_start + EPS, f'{label}: overlapping breaks')
    return bool(intervals)


def check_service(stop, shipment, prefix, facility, arrival, day, label):
    key = stop.shipment_id
    start = numeric(stop.service_start_abs, f'{label} {key} service start')
    end = numeric(stop.service_end_abs, f'{label} {key} service end')
    leave = numeric(stop.departure_abs, f'{label} {key} stop departure')
    require(start >= arrival - EPS and leave >= end - EPS, f'{label}: handling/travel times overlap')
    check_equal(end - start, shipment[f'{prefix}_service_hours'], f'{label} {key} handling duration')
    check_equal(stop.wait_hours, start - arrival, f'{label} {key} wait')
    window_start = 24 * day + max(shipment[f'{prefix}_open_hour'], facility['open_hour'])
    window_end = 24 * day + min(shipment[f'{prefix}_close_hour'], facility['close_hour'])
    require(start >= window_start - EPS and end <= window_end + EPS, f'{label}: {key} handling outside window/facility hours')
    return start, end, leave


def replay_tour(tour, route, tables, lanes, rules, parameters):
    shipments = tables['shipments']
    label = f'Tour {tour.tour_id}'
    tractor_id = tour.tractor_id
    require(tractor_id in tables['tractors'], f'{label}: unknown tractor {tractor_id}')
    tractor = tables['tractors'][tractor_id]
    day = whole_number(tour.day, f'{label} day', 1)
    require(day <= parameters['demand_days'], f'{label}: day outside demand horizon')
    home, equipment = (tractor['home_depot_id'], tractor['equipment_id'])
    require(tour.home_depot_id == home and tour.equipment_id == equipment, f'{label}: wrong fixed depot or equipment')
    capacity, depot = (tables['equipment'][equipment], tables['depots'][home])
    route_stops, breaks = [], []
    for stop in route.itertuples(index=False):
        require(stop.tractor_id == tractor_id, f'{label}: stop has wrong tractor')
        check_equal(stop.day, day, f'{label} stop day')
        require(stop.event in ('DEPART', 'RETURN', 'PICKUP', 'DELIVERY', 'BREAK'),
                f'{label}: unknown stop event')
        (breaks if stop.event == 'BREAK' else route_stops).append(stop)

    require(len(route_stops) >= 4, f'{label}: missing departure, pickup, delivery or return')
    require(route_stops[0].event == 'DEPART' and route_stops[-1].event == 'RETURN', f'{label}: must depart and return')
    require(all((r.event in ('PICKUP', 'DELIVERY') for r in route_stops[1:-1])), f'{label}: extra departure/return')
    require(route_stops[0].location_id == home and route_stops[-1].location_id == home, f'{label}: wrong departure/return depot')
    depart = numeric(route_stops[0].departure_abs, f'{label} departure')
    check_equal(tour.departure_abs, depart, f'{label} departure total')
    earliest = 24 * day + max(depot['open_hour'], tractor['shift_start_hour'])
    latest = min(24 * day + min(depot['close_hour'], tractor['shift_end_hour']), 24 * parameters['horizon_end_day'])
    require(depart >= earliest - EPS, f'{label}: departure before shift/depot opens')
    for column in ['leg_miles', 'leg_drive_hours', 'wait_hours'] + LOAD_COLUMNS:
        check_equal(getattr(route_stops[0], column), 0, f'{label} departure {column}')
    require(pd.isna(route_stops[0].shipment_id), f'{label}: departure has shipment ID')

    current_time, current_location = depart, home
    miles = total_driving = driving_since_break = 0.0
    on_board, picked, delivered = ([], set(), set())
    used_breaks = set()
    load = {'weight_lb': 0.0, 'linear_feet': 0.0, 'pallets': 0.0}
    resources = [
        ('weight_lb', 'load_weight_lb', 'weight_capacity_lb'),
        ('linear_feet', 'load_feet', 'length_capacity_feet'),
        ('pallets', 'load_pallets', 'pallet_capacity'),
    ]

    for stop in route_stops[1:]:
        next_location = stop.location_id
        require(next_location in tables['facilities'] or next_location == home, f'{label}: unknown/wrong stop location {next_location}')
        leg_miles, leg_hours = (
            (0, 0) if current_location == next_location else lanes[current_location, next_location]
        )
        require(driving_since_break + leg_hours <= rules['break_after_drive_hours'] + EPS, f'{label}: driving exceeds break threshold before {next_location}')
        arrival = current_time + leg_hours
        check_equal(stop.arrival_abs, arrival, f'{label} arrival at {next_location}')
        check_equal(stop.leg_miles, leg_miles, f'{label} leg miles')
        check_equal(stop.leg_drive_hours, leg_hours, f'{label} leg drive hours')
        miles += leg_miles
        total_driving += leg_hours
        driving_since_break += leg_hours

        if stop.event == 'RETURN':
            require(not on_board, f'{label}: returns with freight on board')
            require(pd.isna(stop.shipment_id), f'{label}: return has shipment ID')
            for column in LOAD_COLUMNS + ['wait_hours']:
                check_equal(getattr(stop, column), 0, f'{label} return {column}')
            current_time = arrival
            continue

        key = stop.shipment_id
        require(key in shipments, f'{label}: unknown shipment {key}')
        shipment = shipments[key]
        require(shipment['day'] == day, f'{label}: {key} served on wrong day')
        pickup = stop.event == 'PICKUP'
        prefix = 'pickup' if pickup else 'delivery'
        expected_location = shipment['origin_id'] if pickup else shipment['destination_id']
        require(next_location == expected_location, f'{label}: {key} wrong {prefix} facility')
        start, end, leave = check_service(
            stop, shipment, prefix, tables['facilities'][next_location], arrival, day, label
        )
        if check_breaks(breaks, used_breaks, next_location, (arrival, start, end, leave),
                        rules['break_hours'], label):
            driving_since_break = 0.0

        if pickup:
            require(key not in picked, f'{label}: duplicate pickup {key}')
            require(shipment['temperature'] != 'chilled' or capacity['chilled_capable'] == 1, f'{label}: chilled freight on non-chilled equipment')
            for other in on_board:
                for field in ['temperature', 'compatibility_group']:
                    require(shipment[field] == shipments[other][field], f'{label}: incompatible on-board freight')
            on_board.append(key)
            picked.add(key)
            sign = 1
        else:
            require(key in on_board, f'{label}: delivery before pickup or duplicate delivery {key}')
            if parameters['lifo_required']:
                require(on_board[-1] == key, f'{label}: LIFO violation for {key}')
            on_board.remove(key)
            delivered.add(key)
            sign = -1
        for demand_column, report_column, capacity_column in resources:
            load[demand_column] += sign * shipment[demand_column]
            require(-EPS <= load[demand_column] <= capacity[capacity_column] + EPS, f'{label}: {demand_column} capacity exceeded')
            check_equal(getattr(stop, report_column), load[demand_column], f'{label} {key} {report_column}')
        current_time, current_location = (leave, next_location)

    require(len(used_breaks) == len(breaks), f'{label}: break outside a visited stop interval')
    require(picked == delivered and picked, f'{label}: incomplete or empty shipment service')
    listed = ids_from_text(tour.shipments)
    require(len(listed) == len(set(listed)) and set(listed) == picked, f'{label}: shipment list disagrees with actual stops')
    events = len(route_stops) - 2
    require(events <= parameters['max_stops'], f'{label}: too many service events')
    check_equal(tour.num_shipments, len(picked), f'{label} shipment count')
    check_equal(tour.num_events, events, f'{label} event count')
    require(current_time <= latest + EPS, f'{label}: return after shift/depot/horizon closes')
    require(total_driving <= rules['max_drive_hours'] + EPS, f'{label}: daily driving limit exceeded')
    require(current_time - depart <= rules['max_duty_hours'] + EPS, f'{label}: duty limit exceeded')
    for column, expected in [('return_abs', current_time), ('miles', miles), ('drive_hours', total_driving), ('duty_hours', current_time - depart)]:
        check_equal(getattr(tour, column), expected, f'{label} {column}')

    rate = tables['tl_costs'][equipment]
    linehaul = max(rate['minimum_linehaul_cad'], miles * rate['linehaul_cad_per_mile'])
    stop_fee = max(0, events - 2) * rate['extra_stop_cad']
    for column, expected in [('linehaul_cad', linehaul), ('stop_fee_cad', stop_fee), ('tour_cost_cad', linehaul + stop_fee)]:
        check_equal(getattr(tour, column), expected, f'{label} {column}', CENT)
    return depart, current_time, miles, linehaul + stop_fee


def check_route(data, plan):
    # Check the original data before judging the plan.
    tables, rules, parameters, lanes, ltl_costs = check_data(data)
    shipments = tables['shipments']
    # The plan needs route summaries, stop events and outside-carrier assignments.
    required = {
        'tours': 'tour_id tractor_id equipment_id home_depot_id day shipments num_shipments num_events departure_abs return_abs miles drive_hours duty_hours linehaul_cad stop_fee_cad tour_cost_cad',
        'stops': 'tour_id tractor_id day seq event shipment_id location_id arrival_abs service_start_abs service_end_abs departure_abs wait_hours leg_miles leg_drive_hours load_weight_lb load_feet load_pallets',
        'ltl': 'shipment_id day ltl_cost_cad',
    }
    for name, fields in required.items():
        require(name in plan, f'Plan: missing sheet {name}')
        columns(plan[name], fields, f'Plan {name}')
    tours, stops, ltl_shipments = (plan['tours'], plan['stops'], plan['ltl'])
    # Reject repeated routes and trucks assigned twice on the same day.
    unique(tours, ['tour_id'], 'Plan tours')
    unique(tours, ['tractor_id', 'day'], 'Plan truck/day')
    unique(stops, ['tour_id', 'seq'], 'Plan stop sequence')
    unique(ltl_shipments, ['shipment_id'], 'Plan LTL')
    require(stops.tour_id.isin(tours.tour_id).all(), 'Plan: stops refer to unknown tour')
    for value in stops['seq']:
        whole_number(value, 'Plan stop sequence')
    ltl_total = truck_total = total_miles = 0.0
    rest_intervals = defaultdict(list)
    # Check the stated day and independently calculated price of each LTL job.
    for row in ltl_shipments.to_dict('records'):
        key = row['shipment_id']
        require(key in shipments, f'LTL: unknown shipment {key}')
        check_equal(row['day'], shipments[key]['day'], f'{key} LTL day')
        check_equal(row['ltl_cost_cad'], ltl_costs[key], f'{key} LTL cost', CENT)
        ltl_total += ltl_costs[key]
    for tour in tours.itertuples(index=False):
        route = stops[stops.tour_id == tour.tour_id].sort_values('seq')
        depart, returned, miles, cost = replay_tour(
            tour, route, tables, lanes, rules, parameters
        )
        rest_intervals[tour.tractor_id].append((depart, returned))
        total_miles += miles
        truck_total += cost
    check_coverage(data, plan)
    # Check rest between one tour's return and the next departure for the same truck.
    for tractor_id, intervals in rest_intervals.items():
        intervals.sort()
        for previous, following in zip(intervals, intervals[1:]):
            require(following[0] - previous[1] >= rules['min_rest_hours'] - EPS, f'{tractor_id}: insufficient rest between tours')
    # Recalculate the all-LTL baseline, total cost and savings.
    baseline = sum(ltl_costs.values())
    total = truck_total + ltl_total
    result = {
        'all_ltl_cad': baseline, 'objective_cad': total, 'savings_cad': baseline - total,
        'savings_pct': 100 * (baseline - total) / baseline if baseline else 0.0,
        'shipments_ltl': len(ltl_shipments),
        'shipments_dedicated': sum(tours.num_shipments == 1),
        'shipments_consolidated': int(tours.loc[tours.num_shipments > 1, 'num_shipments'].sum()),
        'total_miles': total_miles,
    }
    # Compare the recalculated results with the summary, if the export includes one.
    if 'summary' in plan:
        columns(plan['summary'], 'item value', 'Plan summary')
        unique(plan['summary'], ['item'], 'Plan summary')
        summary = plan['summary'].set_index('item')['value'].to_dict()
        for key, expected in result.items():
            if key in summary:
                tolerance = CENT if key.endswith('_cad') or key == 'savings_pct' else EPS
                check_equal(summary[key], expected, f'Summary {key}', tolerance)
    return result

# 7. Run the checker and show the results

def main():
    # Default to the full dataset; optional paths let us check a simplified plan too.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=DATA_FILE)
    parser.add_argument('--plan', type=Path, default=PLAN_FILE)
    args = parser.parse_args()
    try:
        data = load_data(args.data)
        plan = load_plan(args.plan)
        result = check_route(data, plan)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f'FAIL: {exc}\n')
    print("PASS: all implemented feasibility and cost checks passed")
    print(f"Data file: {args.data.name}")
    print(f"Plan file: {args.plan.name}")
    print(f"Workbook: {len(data['shipments'])} shipments, "
          f"{len(data['tractors'])} tractors")
    print(f"Plan: {len(plan['tours'])} tours, "
          f"{len(plan['ltl'])} shipments sent by LTL")
    print(f"Event records: {len(plan['stops'])} "
          "(pickups, deliveries, depot departures/returns and breaks)")

    print("\nIndependently recalculated costs:")
    print(f"  All-LTL baseline: CAD {result['all_ltl_cad']:,.2f}")
    print(f"  Submitted plan:   CAD {result['objective_cad']:,.2f}")
    print(f"  Savings:          CAD {result['savings_cad']:,.2f} "
          f"({result['savings_pct']:.2f}%)")

    print("\nThis validates the submitted plan's feasibility and costs.")
    print("It does not prove optimality or check for missing candidate routes.")


if __name__ == '__main__':
    main()
