"""Run homr without collapsing a system into one staff.

Three changes, all applied in this process because homr is a subprocess:

- The curly piano brace joins the bottom two staves of its own system.
  A square choir bracket does not join two voices.
- A left-hand mark that covers one whole system is that system. When a
  second one is glued on by a barline, the two systems follow each other.
- Systems with different staff counts stay side by side. A missing staff
  at the top, such as S.A. resting, becomes rests, so T.B. and the piano
  do not slide into the wrong part.
"""
import homr.brace_dot_detection as brace_detection
import homr.main as homr_main
import homr.model as model
import homr.staff_parsing as staff_parsing
from homr.simple_logging import eprint
from homr.transformer.vocabulary import EncodedSymbol, remove_duplicated_symbols


def _covers(symbol, staff, unit):
    top = symbol.center[1] - symbol.size[1] / 2
    bottom = symbol.center[1] + symbol.size[1] / 2
    return staff.max_y >= top - unit and staff.min_y <= bottom + unit


def _select_brace_pairs(self, brace_dot):
    """The curly brace is the piano, so it joins the bottom two staves of its system.

    A square choir bracket is thinner than a barline unit and must not join
    two voices. A brace box often stops short of the lower staff; the piano
    is still the bottom pair, not whichever pair sits fully inside the box.
    """
    if len(self.staffs) < 2:
        return []
    unit = self.staffs[0].average_unit_size
    wide = [symbol for symbol in brace_dot if symbol.size[0] > unit * 1.2]
    bottom = self.staffs[-2:]
    if any(_covers(symbol, staff, unit) for symbol in wide for staff in bottom):
        last = len(self.staffs) - 1
        return [model.MultiStaff.GrandStaffPair([last - 1, last], 1.0)]
    return []


def _bracket_ranges(system, brace_dot):
    """A left-hand mark that covers a whole choir-plus-piano is one system.

    Barlines only reach the next staff. The real system mark sits on the
    left and covers at least four staves, so two of them mean the next
    system was glued on. A mark that covers every staff is the page edge.
    """
    staffs = list(system.staffs)
    if len(staffs) < 8:
        return []
    unit = staffs[0].average_unit_size
    left = min(staff.min_x for staff in staffs)
    ranges = []
    for symbol in brace_dot:
        if symbol.center[0] > left + unit * 3:
            continue
        covered = [index for index, staff in enumerate(staffs) if _covers(symbol, staff, unit)]
        if len(covered) < 4 or len(covered) >= len(staffs):
            continue
        ranges.append((covered[0], covered[-1]))
    ranges.sort()
    merged = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _split_glued(staffs, brace_dot):
    """A barline can glue the next system on. Each choir bracket starts its own system."""
    output = []
    for system in staffs:
        ranges = _bracket_ranges(system, brace_dot)
        ordered = list(system.staffs)
        if len(ranges) < 2:
            output.append(system)
            continue
        chunks = []
        if ranges[0][0] > 0:
            chunks.append(ordered[: ranges[0][0]])
        for index, (start, _end) in enumerate(ranges):
            next_start = ranges[index + 1][0] if index + 1 < len(ranges) else len(ordered)
            chunks.append(ordered[start:next_start])
        eprint("Split one tall system into", [len(chunk) for chunk in chunks])
        for chunk in chunks:
            if chunk:
                output.append(model.MultiStaff(chunk, []))
    return [system.create_grandstaffs(brace_dot) for system in output]


model.MultiStaff._select_grandstaffs = _select_brace_pairs
brace_detection._create_grandstaffs = _split_glued


def _bar_count(symbols):
    if not symbols:
        return 1
    bars = sum(1 for symbol in symbols if "barline" in symbol.rhythm or "repeat" in symbol.rhythm)
    return max(1, bars)


def _rest_system(bars):
    symbols = []
    for _ in range(bars):
        symbols.append(EncodedSymbol("rest_1"))
        symbols.append(EncodedSymbol("barline"))
    return symbols


def parse_staffs_aligned(debug, staffs, image, config, selected_staff=-1):
    if not staffs:
        return []
    for system_index, system in enumerate(staffs):
        eprint(
            "system",
            system_index,
            [
                (round(staff.min_y), "grand" if staff.is_grandstaff else "single")
                for staff in system.staffs
            ],
        )
    width = max(len(system.staffs) for system in staffs)
    regions = staff_parsing.StaffRegions(staffs)
    columns = []
    for system in staffs:
        missing = width - len(system.staffs)
        columns.append([None] * missing + list(system.staffs))
    parsed = {}
    running = 0
    for voice_index in range(width):
        for system_index, row in enumerate(columns):
            staff = row[voice_index]
            if staff is None:
                continue
            if selected_staff >= 0 and system_index != selected_staff:
                running += 1
                continue
            symbols = staff_parsing.parse_staff_image(debug, running, staff, image, regions, config)
            parsed[(voice_index, system_index)] = symbols
            running += 1
    voices = []
    for voice_index in range(width):
        result = []
        for system_index in range(len(columns)):
            symbols = parsed.get((voice_index, system_index))
            if symbols:
                result.extend(symbols)
            elif (voice_index, system_index) not in parsed:
                sibling = next(
                    (
                        parsed[(other, system_index)]
                        for other in range(width)
                        if parsed.get((other, system_index))
                    ),
                    None,
                )
                result.extend(_rest_system(_bar_count(sibling)))
            result.append(EncodedSymbol("newline"))
        voices.append(remove_duplicated_symbols(result))
    eprint(
        "Keeping",
        len(voices),
        "parallel staves across",
        len(staffs),
        "systems",
        [len(system.staffs) for system in staffs],
    )
    return voices


homr_main.parse_staffs = parse_staffs_aligned


if __name__ == "__main__":
    homr_main.main()
