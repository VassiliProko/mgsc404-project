"""Check an exported plan against its workbook without importing the optimizer.

Builds on simplified_checker.py's workbook, coverage and truck/day checks.
Run directly for the main plan, or pass --data and --plan for another Excel plan.
A pass proves feasibility under these checks, not optimality.

Reading guide:
  1. Small helper functions explain how we test a value.
  2. check_data() checks the original Excel inputs and rebuilds LTL prices.
  3. check_plan() follows every truck from departure to return, then adds costs.
  4. main() reads the files and prints PASS or the first reason for failure.

LTL means an outside carrier. A tour is one truck's complete daily route.
Service means loading at a pickup or unloading at a delivery.
"""

# import packages
import argparse
from collections import Counter
import math
from pathlib import Path

import pandas as pd

# The checker lives in the project root, beside the data and output folders.
PROJECT_FOLDER = Path(__file__).resolve().parent
# Tiny rounding differences are normal with decimal calculations.
# EPS allows one millionth for times/loads; MONEY_EPS allows about one cent.
EPS = 1e-6
MONEY_EPS = 0.010001

# 1. List the columns needed to check the original data.
# Each dictionary connects a sheet name to its required columns.
# We store column names in strings; .split() turns each string into a list.
# DATA_NUMBERS requires real numbers. DATA_TEXT requires nonblank text.
DATA_NUMBERS = {
    'shipments': 'day pickup_open_hour pickup_close_hour delivery_open_hour delivery_close_hour weight_lb linear_feet pallets freight_class pickup_service_hours delivery_service_hours',
    'facilities': 'open_hour close_hour',
    'depots': 'open_hour close_hour',
    'distance_matrix': 'distance_miles drive_hours',
    'ltl_rates': 'freight_class weight_from_lb weight_to_lb distance_from_miles distance_to_miles base_cost_cad fuel_surcharge_pct',
    'tl_costs': 'linehaul_cad_per_mile minimum_linehaul_cad extra_stop_cad',
    'equipment': 'weight_capacity_lb length_capacity_feet pallet_capacity chilled_capable',
    'tractors': 'shift_start_hour shift_end_hour',
    'driver_rules': 'value',
    'parameters': 'value',
}
DATA_TEXT = {
    'shipments': 'shipment_id origin_id destination_id temperature compatibility_group',
    'facilities': 'facility_id', 'depots': 'depot_id',
    'distance_matrix': 'from_id to_id', 'ltl_rates': '',
    'tl_costs': 'equipment_id', 'equipment': 'equipment_id',
    'tractors': 'tractor_id home_depot_id equipment_id',
    'driver_rules': 'parameter', 'parameters': 'parameter',
}
# A key is the ID used to find a row, such as S001 for a shipment.
# These ID columns must have no blanks and no duplicate values.
KEYS = {
    'shipments': 'shipment_id', 'facilities': 'facility_id', 'depots': 'depot_id',
    'equipment': 'equipment_id', 'tractors': 'tractor_id', 'tl_costs': 'equipment_id',
    'driver_rules': 'parameter', 'parameters': 'parameter',
}


def require(condition, message):
    """Stop immediately with an understandable explanation."""
    # Every check uses this: a false condition stops the checker with this message.
    if not condition:
        raise ValueError(message)


def read_number(value, label):
    """Reject blank cells, text, True/False, and infinite values; return a number."""
    require(pd.notna(value) and not isinstance(value, (str, bool)),
            f'{label}: expected a numeric cell, got {value!r}')
    value = float(value)
    require(math.isfinite(value), f'{label}: number must be finite')
    return value


def check_equal(actual, expected, label, tolerance=EPS):
    """Compare a reported number with the value we independently calculated."""
    actual = read_number(actual, label)
    require(abs(actual - expected) <= tolerance,
            f'{label}: reported {actual:g}, expected {expected:g}')


def read_integer(value, label, minimum=0):
    """Check whole-number fields: for example, a shipment cannot need 1.5 pallets."""
    value = read_number(value, label)
    require(value.is_integer() and value >= minimum,
            f'{label}: expected an integer >= {minimum}')
    return int(value)


def check_columns(frame, needed, label):
    """Check that a sheet contains every required column, regardless of order."""
    # Sets hold distinct values. Subtracting sets finds the missing column names.
    missing = set(needed.split()) - set(frame.columns)
    require(not missing, f'{label}: missing columns {sorted(missing)}')


def check_unique(frame, keys, label):
    """Reject blank or repeated IDs, including pairs such as tractor + day."""
    require(not frame[keys].isna().any().any(), f'{label}: blank key')
    require(not frame.duplicated(keys).any(), f'{label}: duplicate {keys}')


def check_hours(start, end, label):
    """Opening must come before closing, and both must be within the same day."""
    require(0 <= start < end <= 24, f'{label}: hours must satisfy 0 <= open < close <= 24')


def read_workbook(path):
    """Read every Excel sheet into a dictionary: sheet name -> pandas table."""
    return pd.read_excel(path, sheet_name=None)


# 2. Check input values before using them to judge a plan.
def check_data(data):
    """Check input quality and return lookup tables, operating rules and prices."""
    # Check the sheet/column names first, then each numeric cell and text cell.
    # idx + 2 gives the Excel row: Python starts at 0 and Excel has a header row.
    for name in DATA_NUMBERS:
        require(name in data, f'Data: missing sheet {name}')
        frame = data[name]
        check_columns(frame, DATA_NUMBERS[name] + ' ' + DATA_TEXT[name], name)
        for column in DATA_NUMBERS[name].split():
            for idx, value in frame[column].items():
                read_number(value, f'{name}.{column}, Excel row {idx + 2}')
        for column in DATA_TEXT[name].split():
            for value in frame[column]:
                require(isinstance(value, str) and bool(value.strip()),
                        f'{name}.{column}: IDs and labels must be nonblank text')
        if name in KEYS:
            check_unique(frame, [KEYS[name]], name)

    # Convert sheets into dictionaries so we can look up rows by their actual IDs.
    # Example: tables['shipments']['S001']['weight_lb'] gives S001's weight.
    tables = {}
    for name, key in KEYS.items():
        tables[name] = data[name].set_index(key).to_dict('index')
    shipments, facilities, depot = tables['shipments'], tables['facilities'], tables['depots']
    equipment_types, tractors = tables['equipment'], tables['tractors']
    # Read rules by name instead of assuming they appear in a particular row.
    # Example: rules['max_drive_hours'] reads the workbook's driving limit.
    rules = data['driver_rules'].set_index('parameter')['value'].to_dict()
    parameters = data['parameters'].set_index('parameter')['value'].to_dict()
    for key in 'max_drive_hours max_duty_hours min_rest_hours break_after_drive_hours break_hours'.split():
        require(key in rules and rules[key] > 0, f'Driver rule {key}: missing or not positive')
    for key in 'demand_days horizon_end_day time_step_hours average_speed_mph max_stops lifo_required'.split():
        require(key in parameters, f'Parameters: missing {key}')
    # Check that the planning days, speed, time step and LIFO switch make sense.
    days = read_integer(parameters['demand_days'], 'demand_days', 1)
    horizon = read_integer(parameters['horizon_end_day'], 'horizon_end_day', 1)
    read_integer(parameters['max_stops'], 'max_stops', 2)
    require(horizon > days, 'Horizon must end after the last demand day')
    require(parameters['time_step_hours'] > 0 and parameters['average_speed_mph'] > 0,
            'Time step and speed must be positive')
    require(parameters['lifo_required'] in (0, 1), 'lifo_required must be 0 or 1')
    require(not set(facilities) & set(depot), 'Facility and depot IDs must be distinct')
    # Sites and depots must have valid daily opening/closing hours.
    for name in ['facilities', 'depots']:
        for key, row in tables[name].items():
            check_hours(row['open_hour'], row['close_hour'], key)
    # A truck needs positive capacities. Chilled capability is either 0 or 1.
    for key, row in equipment_types.items():
        require(all(row[c] > 0 for c in ['weight_capacity_lb', 'length_capacity_feet', 'pallet_capacity']),
                f'{key}: capacities must be positive')
        read_integer(row['pallet_capacity'], f'{key} pallet capacity', 1)
        require(row['chilled_capable'] in (0, 1), f'{key}: chilled_capable must be 0 or 1')
    # Every TL cost row must refer to a known equipment type with nonnegative fees.
    for key, row in tables['tl_costs'].items():
        require(key in equipment_types, f'{key}: cost refers to unknown equipment')
        require(all(row[c] >= 0 for c in DATA_NUMBERS['tl_costs'].split()), f'{key}: negative TL cost')
    # Each tractor must refer to a real depot and equipment with a known price.
    for key, row in tractors.items():
        require(row['home_depot_id'] in depot, f'{key}: unknown home depot')
        require(row['equipment_id'] in equipment_types and row['equipment_id'] in tables['tl_costs'],
                f'{key}: missing equipment or TL cost')
        check_hours(row['shift_start_hour'], row['shift_end_hour'], key)
    # Check shipment locations, day, time windows, demands and freight labels.
    # Pickup and delivery windows can overlap; each window must still be ordered.
    for key, row in shipments.items():
        require(row['origin_id'] in facilities and row['destination_id'] in facilities, f'{key}: unknown facility')
        require(1 <= read_integer(row['day'], f'{key} day', 1) <= days, f'{key}: day outside horizon')
        for prefix in ['pickup', 'delivery']:
            check_hours(row[f'{prefix}_open_hour'], row[f'{prefix}_close_hour'], f'{key} {prefix}')
        require(all(row[c] > 0 for c in ['weight_lb', 'linear_feet', 'pallets', 'pickup_service_hours', 'delivery_service_hours']),
                f'{key}: demands and handling durations must be positive')
        read_integer(row['pallets'], f'{key} pallets', 1)
        read_integer(row['freight_class'], f'{key} freight class', 1)
        require(row['temperature'] in ('ambient', 'chilled'), f'{key}: unknown temperature')
        require(row['compatibility_group'] in ('general', 'food', 'industrial'), f'{key}: unknown compatibility group')

    # Travel is directed. Check every ordered pair and the stored rounding formula.
    # A -> B and B -> A are separate rows; we cannot assume equal travel times.
    travel = data['distance_matrix']
    check_unique(travel, ['from_id', 'to_id'], 'distance_matrix')
    locations = set(facilities) | set(depot)
    expected = set()
    for origin in locations:
        for destination in locations:
            if origin != destination:
                expected.add((origin, destination))
    # zip pairs each row's origin with its destination. Compare against all needed pairs.
    actual = set(zip(travel.from_id, travel.to_id))
    require(actual == expected, 'distance_matrix: missing or unexpected directed pairs')
    # lanes[(origin, destination)] stores (miles, driving hours) for that direction.
    lanes = {}
    for row in travel.to_dict('records'):
        require(row['distance_miles'] > 0 and row['drive_hours'] > 0, 'Travel distance/time must be positive')
        step = parameters['time_step_hours']
        # Divide miles by speed, then round UP to a whole number of time steps.
        # Example: 115 miles / 50 mph = 2.3 hours -> 2.5 hours with 0.25-hour steps.
        rounded = math.ceil(row['distance_miles'] / parameters['average_speed_mph'] / step - EPS) * step
        check_equal(row['drive_hours'], rounded, f"Travel {row['from_id']} -> {row['to_id']} driving time")
        lanes[row['from_id'], row['to_id']] = (row['distance_miles'], row['drive_hours'])

    # Match LTL bands independently, including their exact boundary rules.
    # First reject invalid intervals and negative prices in the rate sheet.
    rates = data['ltl_rates']
    for row in rates.to_dict('records'):
        read_integer(row['freight_class'], 'LTL freight class', 1)
        require(0 <= row['weight_from_lb'] < row['weight_to_lb'] and
                0 <= row['distance_from_miles'] < row['distance_to_miles'], 'LTL: invalid rate band')
        require(row['base_cost_cad'] >= 0 and row['fuel_surcharge_pct'] >= 0, 'LTL: negative rate')
    # Rebuild an outside-carrier price for EVERY shipment, including those on trucks.
    # This also lets us independently calculate the all-LTL baseline later.
    ltl_costs = {}
    for key, row in shipments.items():
        distance = 0 if row['origin_id'] == row['destination_id'] else lanes[row['origin_id'], row['destination_id']][0]
        # Keep only rate rows with the right class, weight band and direct-distance band.
        # Lower bounds count; upper bounds do not. '&' joins pandas filtering conditions.
        matches = rates[(rates.freight_class == row['freight_class']) &
                        (rates.weight_from_lb <= row['weight_lb']) & (row['weight_lb'] < rates.weight_to_lb) &
                        (rates.distance_from_miles <= distance) & (distance < rates.distance_to_miles)]
        require(len(matches) == 1, f'{key}: expected one LTL rate, found {len(matches)}')
        # iloc[0] reads the single matched row AFTER checking there is exactly one.
        rate = matches.iloc[0]
        ltl_costs[key] = rate.base_cost_cad * (1 + rate.fuel_surcharge_pct)
    return tables, rules, parameters, lanes, ltl_costs


# 3. Check the exported sheet structure and coverage lists.
def check_plan(data, plan):
    """Recalculate feasibility and costs from the data and exported Excel events."""
    # No optimizer functions or solver results are used to decide whether a route works.
    tables, rules, parameters, lanes, ltl_costs = check_data(data)
    shipments, tractors = tables['shipments'], tables['tractors']
    # tours summarizes routes; stops records events; ltl lists outside-carrier jobs.
    required = {
        'tours': 'tour_id tractor_id equipment_id home_depot_id day shipments num_shipments num_events departure_abs return_abs miles drive_hours duty_hours linehaul_cad stop_fee_cad tour_cost_cad',
        'stops': 'tour_id tractor_id day seq event shipment_id location_id arrival_abs service_start_abs service_end_abs departure_abs wait_hours leg_miles leg_drive_hours load_weight_lb load_feet load_pallets',
        'ltl': 'shipment_id day ltl_cost_cad',
    }
    for name, fields in required.items():
        require(name in plan, f'Plan: missing sheet {name}')
        check_columns(plan[name], fields, f'Plan {name}')
    tours, stops, ltl_shipments = plan['tours'], plan['stops'], plan['ltl']
    # Check for repeated routes, double-booked trucks, repeated stops and repeated LTL jobs.
    check_unique(tours, ['tour_id'], 'Plan tours')
    check_unique(tours, ['tractor_id', 'day'], 'Plan truck/day')
    check_unique(stops, ['tour_id', 'seq'], 'Plan stop sequence')
    check_unique(ltl_shipments, ['shipment_id'], 'Plan LTL')
    require(stops.tour_id.isin(tours.tour_id).all(), 'Plan: stops refer to unknown tour')
    # Validate sequence numbers before sorting, so text cannot change stop order.
    for value in stops['seq']:
        read_integer(value, 'Plan stop sequence')
    # Counter starts each shipment at zero and counts one completed service at a time.
    # rest_intervals will store each tractor's departure/return pairs across the week.
    service_counts = Counter()
    ltl_total = truck_total = total_miles = 0.0
    rest_intervals = {tractor_id: [] for tractor_id in tractors}
    # Check outside-carrier jobs first: known shipment, correct day and correct bill.
    # to_dict('records') gives one ordinary Python dictionary per spreadsheet row.
    for row in ltl_shipments.to_dict('records'):
        key = row['shipment_id']
        require(key in shipments, f'LTL: unknown shipment {key}')
        check_equal(row['day'], shipments[key]['day'], f'{key} LTL day')
        check_equal(row['ltl_cost_cad'], ltl_costs[key], f'{key} LTL cost', MONEY_EPS)
        service_counts[key] += 1
        ltl_total += ltl_costs[key]

    # 4. Rebuild each route from the actual pickup and delivery events.
    for tour in tours.to_dict('records'):
        # Look up this truck's fixed equipment, home depot and assigned day.
        label = f"Tour {tour['tour_id']}"
        tractor_id = tour['tractor_id']
        require(tractor_id in tractors, f'{label}: unknown tractor {tractor_id}')
        tractor = tractors[tractor_id]
        day = read_integer(tour['day'], f'{label} day', 1)
        require(day <= parameters['demand_days'], f'{label}: day outside demand horizon')
        home, equipment = tractor['home_depot_id'], tractor['equipment_id']
        require(tour['home_depot_id'] == home and tour['equipment_id'] == equipment,
                f'{label}: wrong fixed depot or equipment')
        capacity, depot = tables['equipment'][equipment], tables['depots'][home]
        # Select only this tour's stops, then put them in the recorded visiting order.
        route = stops[stops.tour_id == tour['tour_id']].sort_values('seq')
        require(len(route) >= 4, f'{label}: route needs departure, pickup, delivery and return')
        for row in route.to_dict('records'):
            read_integer(row['seq'], f'{label} stop sequence')
            require(row['tractor_id'] == tractor_id, f'{label}: stop has wrong tractor')
            check_equal(row['day'], day, f'{label} stop day')
            require(row['event'] in ('DEPART', 'RETURN', 'PICKUP', 'DELIVERY', 'BREAK'), f'{label}: unknown stop event')
        # Breaks are checked by their TIMES, separately from the visiting order.
        # The export may list a break after a service row even if it occurred while waiting.
        route_stops = route[route.event != 'BREAK'].to_dict('records')
        breaks = route[route.event == 'BREAK'].to_dict('records')
        require(len(route_stops) >= 4, f'{label}: missing departure, pickup, delivery or return')
        require(route_stops[0]['event'] == 'DEPART' and route_stops[-1]['event'] == 'RETURN', f'{label}: must depart and return')
        require(all(r['event'] in ('PICKUP', 'DELIVERY') for r in route_stops[1:-1]), f'{label}: extra departure/return')
        require(route_stops[0]['location_id'] == home and route_stops[-1]['location_id'] == home, f'{label}: wrong departure/return depot')
        # Absolute times use 24 * day + local hour. Day 1 at 07:30 is 31.5.
        # Departure must fit both depot hours and the driver's shift.
        depart = read_number(route_stops[0]['departure_abs'], f'{label} departure')
        check_equal(tour['departure_abs'], depart, f'{label} departure total')
        earliest = 24 * day + max(depot['open_hour'], tractor['shift_start_hour'])
        latest = min(24 * day + min(depot['close_hour'], tractor['shift_end_hour']), 24 * parameters['horizon_end_day'])
        require(depart >= earliest - EPS, f'{label}: departure before shift/depot opens')
        # Before leaving home there has been no travel, waiting or freight loading.
        for column in ['leg_miles', 'leg_drive_hours', 'load_weight_lb', 'load_feet', 'load_pallets', 'wait_hours']:
            check_equal(route_stops[0][column], 0, f'{label} departure {column}')
        require(pd.isna(route_stops[0]['shipment_id']), f'{label}: departure has shipment ID')
        # Start following the truck. These values are updated after each stop.
        # on_board keeps loading order; picked/delivered remember which jobs occurred.
        current_time, current_location = depart, home
        miles = total_driving = driving_since_break = 0.0
        on_board, picked, delivered = [], set(), set()
        used_breaks = set()
        load = {'weight_lb': 0.0, 'linear_feet': 0.0, 'pallets': 0.0}
        # Each entry links a shipment demand to its reported load and truck limit.
        resources = [
            ('weight_lb', 'load_weight_lb', 'weight_capacity_lb'),
            ('linear_feet', 'load_feet', 'length_capacity_feet'),
            ('pallets', 'load_pallets', 'pallet_capacity'),
        ]
        for stop in route_stops[1:]:
            # Recalculate this travel leg from the original distance matrix.
            # Staying at the same location takes zero miles and zero driving time.
            next_location = stop['location_id']
            require(next_location in tables['facilities'] or next_location == home, f'{label}: unknown/wrong stop location {next_location}')
            if current_location == next_location:
                leg_miles, leg_hours = 0, 0
            else:
                leg_miles, leg_hours = lanes[current_location, next_location]
            # A break after arrival cannot fix too much driving on the way there.
            # That is why this check happens BEFORE considering this stop's breaks.
            require(driving_since_break + leg_hours <= rules['break_after_drive_hours'] + EPS,
                    f'{label}: driving exceeds break threshold before {next_location}')
            # The truck must arrive when its previous departure + travel predicts.
            # Add this leg to total driving, distance and driving since the last break.
            arrival = current_time + leg_hours
            check_equal(stop['arrival_abs'], arrival, f'{label} arrival at {next_location}')
            check_equal(stop['leg_miles'], leg_miles, f'{label} leg miles')
            check_equal(stop['leg_drive_hours'], leg_hours, f'{label} leg drive hours')
            miles += leg_miles
            total_driving += leg_hours
            driving_since_break += leg_hours
            # At the final depot return there must be no freight left to deliver.
            # continue skips pickup/delivery processing for this return row.
            if stop['event'] == 'RETURN':
                require(not on_board, f'{label}: returns with freight on board')
                require(pd.isna(stop['shipment_id']), f'{label}: return has shipment ID')
                for column in ['load_weight_lb', 'load_feet', 'load_pallets', 'wait_hours']:
                    check_equal(stop[column], 0, f'{label} return {column}')
                current_time = arrival
                continue
            # For a pickup/delivery, look up the original shipment and its stated day.
            key = stop['shipment_id']
            require(key in shipments, f'{label}: unknown shipment {key}')
            shipment = shipments[key]
            require(shipment['day'] == day, f'{label}: {key} served on wrong day')
            pickup = stop['event'] == 'PICKUP'
            # Pickups use origin + pickup windows; deliveries use destination + delivery windows.
            if pickup:
                prefix = 'pickup'
                expected_location = shipment['origin_id']
            else:
                prefix = 'delivery'
                expected_location = shipment['destination_id']
            require(next_location == expected_location, f'{label}: {key} wrong {prefix} facility')

            # Check arrival -> service start -> service end -> departure in that order.
            # Handling must take the full workbook duration; waiting is start minus arrival.
            start = read_number(stop['service_start_abs'], f'{label} {key} service start')
            end = read_number(stop['service_end_abs'], f'{label} {key} service end')
            leave = read_number(stop['departure_abs'], f'{label} {key} stop departure')
            require(start >= arrival - EPS and leave >= end - EPS, f'{label}: handling/travel times overlap')
            check_equal(end - start, shipment[f'{prefix}_service_hours'], f'{label} {key} handling duration')
            check_equal(stop['wait_hours'], start - arrival, f'{label} {key} wait')
            # Use the stricter of the shipment's window and the facility's opening hours.
            # Finishing at closing is allowed; merely arriving by closing is not enough.
            facility = tables['facilities'][next_location]
            window_start = 24 * day + max(shipment[f'{prefix}_open_hour'], facility['open_hour'])
            window_end = 24 * day + min(shipment[f'{prefix}_close_hour'], facility['close_hour'])
            require(start >= window_start - EPS and end <= window_end + EPS, f'{label}: {key} handling outside window/facility hours')

            # 5. A recorded break must fit at this stop and cannot include handling.
            intervals = []
            for idx, break_row in enumerate(breaks):
                # enumerate gives each break an index. used_breaks prevents counting it twice.
                # Skip breaks already checked or occurring at another location.
                if idx in used_breaks or break_row['location_id'] != next_location:
                    continue
                b_start = read_number(break_row['service_start_abs'], f'{label} break start')
                b_end = read_number(break_row['service_end_abs'], f'{label} break end')
                # A break belongs here only if it fits between arrival and departure.
                # Otherwise a later stop may match it; unmatched breaks fail after the route.
                if b_start < arrival - EPS or b_end > leave + EPS:
                    continue
                # The whole break must be before or after handling, with no travel.
                # A recorded off-duty break can use waiting time; waiting alone does not count.
                require(b_end - b_start >= rules['break_hours'] - EPS, f'{label}: break too short')
                require(b_end <= start + EPS or b_start >= end - EPS, f'{label}: break overlaps handling')
                require(pd.isna(break_row['shipment_id']), f'{label}: break has shipment ID')
                for column in ['leg_miles', 'leg_drive_hours']:
                    check_equal(break_row[column], 0, f'{label} break {column}')
                intervals.append((b_start, b_end))
                used_breaks.add(idx)
            # Sort the break times, then check one break finishes before the next starts.
            intervals.sort()
            previous_end = None
            for break_start, break_end in intervals:
                if previous_end is not None:
                    require(previous_end <= break_start + EPS, f'{label}: overlapping breaks')
                previous_end = break_end
            if intervals:
                # A valid break resets only driving SINCE a break, not daily driving/duty.
                driving_since_break = 0.0

            # Loading uses a stack when LIFO is required; capacity is checked at every event.
            if pickup:
                # A pickup adds freight. Chilled freight needs a refrigerated truck.
                require(key not in picked, f'{label}: duplicate pickup {key}')
                require(shipment['temperature'] != 'chilled' or capacity['chilled_capable'] == 1, f'{label}: chilled freight on non-chilled equipment')
                for other in on_board:
                    # Only freight currently on board must match, not all jobs on the route.
                    for field in ['temperature', 'compatibility_group']:
                        require(shipment[field] == shipments[other][field],
                                f'{label}: incompatible on-board freight')
                on_board.append(key)
                picked.add(key)
                sign = 1  # Add shipment demands to the truck's current load.
            else:
                # A delivery removes freight that must already be on this same truck.
                require(key in on_board, f'{label}: delivery before pickup or duplicate delivery {key}')
                if parameters['lifo_required']:
                    # [-1] is the last item loaded: it must be the first unloaded.
                    require(on_board[-1] == key, f'{label}: LIFO violation for {key}')
                on_board.remove(key)
                delivered.add(key)
                sign = -1  # Subtract shipment demands from the truck's current load.

            # Rebuild weight, floor length and pallets separately after this service event.
            # Check both the truck limit and the load number reported in the plan.
            for demand_column, report_column, capacity_column in resources:
                load[demand_column] += sign * shipment[demand_column]
                require(-EPS <= load[demand_column] <= capacity[capacity_column] + EPS,
                        f'{label}: {demand_column} capacity exceeded')
                check_equal(stop[report_column], load[demand_column],
                            f'{label} {key} {report_column}')
            # The next leg begins from this stop's departure time and location.
            current_time, current_location = leave, next_location

        # Every recorded break must have matched a real stop interval.
        # Every collected shipment must have been delivered before the tour finished.
        require(len(used_breaks) == len(breaks), f'{label}: break outside a visited stop interval')
        require(picked == delivered and picked, f'{label}: incomplete or empty shipment service')
        # Compare actual events with the tour summary; its shipment list is not trusted alone.
        listed = [part.strip() for part in str(tour['shipments']).split(',')]
        require(len(listed) == len(set(listed)) and set(listed) == picked, f'{label}: shipment list disagrees with actual stops')
        # Departure/return are not service stops. Each pickup/delivery is one event.
        events = len(route_stops) - 2
        require(events <= parameters['max_stops'], f'{label}: too many service events')
        check_equal(tour['num_shipments'], len(picked), f'{label} shipment count')
        check_equal(tour['num_events'], events, f'{label} event count')
        # Duty is all elapsed time: driving + handling + waiting + breaks.
        # Check final daily limits, then compare the export's totals with our own totals.
        require(current_time <= latest + EPS, f'{label}: return after shift/depot/horizon closes')
        require(total_driving <= rules['max_drive_hours'] + EPS, f'{label}: daily driving limit exceeded')
        require(current_time - depart <= rules['max_duty_hours'] + EPS, f'{label}: duty limit exceeded')
        for column, expected in [('return_abs', current_time), ('miles', miles), ('drive_hours', total_driving), ('duty_hours', current_time - depart)]:
            check_equal(tour[column], expected, f'{label} {column}')
        # Save this truck's working interval for the overnight-rest check below.
        rest_intervals[tractor_id].append((depart, current_time))
        for shipment_id in picked:
            service_counts[shipment_id] += 1

        # 6. Calculate the full route bill, including empty depot legs.
        # Linehaul is the larger of the minimum charge and miles * per-mile rate.
        # The first two service events are included; each further event adds a stop fee.
        rate = tables['tl_costs'][equipment]
        linehaul = max(rate['minimum_linehaul_cad'], miles * rate['linehaul_cad_per_mile'])
        stop_fee = max(0, events - 2) * rate['extra_stop_cad']
        for column, expected in [('linehaul_cad', linehaul), ('stop_fee_cad', stop_fee), ('tour_cost_cad', linehaul + stop_fee)]:
            check_equal(tour[column], expected, f'{label} {column}', MONEY_EPS)
        truck_total += linehaul + stop_fee
        total_miles += miles

    # Combine actual truck service with LTL service: each shipment must appear exactly once.
    require(set(service_counts) == set(shipments) and all(service_counts[key] == 1 for key in shipments),
            'Coverage: every shipment must have one complete truck service OR one LTL assignment')
    # For each truck, compare one tour's return with the next tour's departure.
    # Each pair is (departure, return), so [0] means departure and [1] means return.
    for tractor_id, intervals in rest_intervals.items():
        intervals.sort()
        for previous, following in zip(intervals, intervals[1:]):
            require(following[0] - previous[1] >= rules['min_rest_hours'] - EPS, f'{tractor_id}: insufficient rest between tours')
    # Add every shipment's LTL price for the baseline, then compare with the checked plan.
    baseline = sum(ltl_costs.values())
    total = truck_total + ltl_total
    result = {'all_ltl_cad': baseline, 'objective_cad': total, 'savings_cad': baseline - total,
              'savings_pct': 100 * (baseline - total) / baseline if baseline else 0.0,
              'shipments_ltl': len(ltl_shipments), 'shipments_dedicated': sum(tours.num_shipments == 1),
              'shipments_consolidated': int(tours.loc[tours.num_shipments > 1, 'num_shipments'].sum()),
              'total_miles': total_miles}
    # If a summary sheet exists, check its financial and shipment totals too.
    # Solver status/gap is not evidence of feasibility and is not used here.
    if 'summary' in plan:
        check_columns(plan['summary'], 'item value', 'Plan summary')
        check_unique(plan['summary'], ['item'], 'Plan summary')
        summary = plan['summary'].set_index('item')['value'].to_dict()
        for key, expected in result.items():
            if key in summary:
                tolerance = MONEY_EPS if key.endswith('_cad') or key == 'savings_pct' else EPS
                check_equal(summary[key], expected, f'Summary {key}', tolerance)
    return result


# 7. Print a short report and return a failing exit code if any check fails.
def main():
    """Choose the files, run all checks, and print a short result."""
    # Defaults find the project's main files even if this script is run from another folder.
    # --data and --plan let the user choose a different workbook/plan without editing code.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=PROJECT_FOLDER / 'data/project1_main.xlsx')
    parser.add_argument('--plan', type=Path, default=PROJECT_FOLDER / 'output/plan_main.xlsx')
    args = parser.parse_args()
    # Stop at the first problem. Exit code 1 tells the terminal that validation failed.
    try:
        result = check_plan(read_workbook(args.data), read_workbook(args.plan))
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f'FAIL: {exc}\n')
    # Reaching this point means every check passed; report our recalculated costs.
    print(f'PASS: {args.plan.name} against {args.data.name}')
    print('Input data, coverage, routes, loading, timing, breaks, rest and costs: OK')
    print(f"Recalculated cost: CAD {result['objective_cad']:,.2f}")
    print(f"All-LTL baseline: CAD {result['all_ltl_cad']:,.2f}")
    print(f"Savings: CAD {result['savings_cad']:,.2f} ({result['savings_pct']:.2f}%)")
    print('This checks feasibility and costs; it does not prove optimality.')


# Run main() when this file is launched as a script.
if __name__ == '__main__':
    main()
