"""Constraint checking for the B8 planning sub-family.

Shared by the reference solver (to search) and the scorer (to judge), so that a
plan is never accepted on the solver's own word. The rules are intentionally
small and declarative; a constraint the checker cannot express is a constraint
the family does not test.
"""
from __future__ import annotations

from typing import Any

CONSTRAINT_KINDS = {"before", "after", "at", "different_rooms"}


def slot_index(task: dict[str, Any]) -> dict[str, int]:
    return {slot: index for index, slot in enumerate(task["slots"])}


def validate_assignment(task: dict[str, Any], assignment: Any) -> list[str]:
    """Return every constraint violation in an assignment (empty list == valid)."""
    problems: list[str] = []
    if not isinstance(assignment, dict) or not assignment:
        return ["no assignment produced"]

    rooms = {room["room_id"]: room for room in task["rooms"]}
    meetings = {m["meeting_id"]: m for m in task["meetings"]}
    index = slot_index(task)
    last_slot = len(task["slots"]) - 1

    for meeting_id in meetings:
        entry = assignment.get(meeting_id)
        if not isinstance(entry, dict) or "room" not in entry or "slot" not in entry:
            problems.append(f"{meeting_id} unassigned")
            continue
        if entry["room"] not in rooms:
            problems.append(f"{meeting_id} uses unknown room {entry['room']!r}")
        if entry["slot"] not in index:
            problems.append(f"{meeting_id} uses unknown slot {entry['slot']!r}")
    for extra in set(assignment) - set(meetings):
        problems.append(f"assignment names unknown meeting {extra!r}")
    if problems:
        return problems

    for meeting_id, meeting in meetings.items():
        entry = assignment[meeting_id]
        room = rooms[entry["room"]]
        start = index[entry["slot"]]
        duration = int(meeting["duration_h"])
        if room["capacity"] < meeting["min_capacity"]:
            problems.append(
                f"{meeting_id} needs capacity {meeting['min_capacity']} but "
                f"{entry['room']} holds {room['capacity']}")
        if start + duration - 1 > last_slot:
            problems.append(f"{meeting_id} runs past the last slot {task['slots'][-1]}")
            continue
        busy = set(room.get("busy", []))
        for offset in range(duration):
            slot = task["slots"][start + offset]
            if slot in busy:
                problems.append(f"{meeting_id} overlaps a busy slot {slot} in {entry['room']}")

    occupied: dict[tuple[str, int], str] = {}
    for meeting_id, meeting in meetings.items():
        entry = assignment[meeting_id]
        start = index[entry["slot"]]
        for offset in range(int(meeting["duration_h"])):
            key = (entry["room"], start + offset)
            if key in occupied:
                problems.append(
                    f"{meeting_id} overlaps {occupied[key]} in {entry['room']} at "
                    f"{task['slots'][start + offset]}")
            else:
                occupied[key] = meeting_id

    for constraint in task["constraints"]:
        kind = constraint["kind"]
        if kind == "before":
            if index[assignment[constraint["meeting"]]["slot"]] >= index[constraint["slot"]]:
                problems.append(
                    f"{constraint['meeting']} must start before {constraint['slot']}")
        elif kind == "after":
            if index[assignment[constraint["meeting"]]["slot"]] <= index[constraint["slot"]]:
                problems.append(
                    f"{constraint['meeting']} must start after {constraint['slot']}")
        elif kind == "at":
            if index[assignment[constraint["meeting"]]["slot"]] != index[constraint["slot"]]:
                problems.append(
                    f"{constraint['meeting']} must start exactly at {constraint['slot']}")
        elif kind == "different_rooms":
            first, second = constraint["meetings"][:2]
            if assignment[first]["room"] == assignment[second]["room"]:
                problems.append(f"{first} and {second} must be in different rooms")
    return problems
