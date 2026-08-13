"""Phase 2C: one-call patch interpretation and deterministic local patching."""

import asyncio
import json
import re
from typing import Any, Optional

from ..models.schemas import (
    AddPOIOperation, Attraction, Location, Meal, PatchOperation, RemovePOIOperation,
    ReplacePOIOperation, TripChangeDiff, TripPatch, TripPlan, TripRequest,
    UpdateDayPaceOperation, UpdateMealOperation, UpdateStartTimeOperation,
    UpdateTransportOperation,
)
from .llm_service import create_chat_completion, get_llm


PATCH_MAX_LLM_CALLS = 1
PATCH_MAX_COMPLETION_TOKENS = 1000


class TripPatchError(ValueError):
    """A patch cannot be safely applied to the current plan."""


def _json_object(text: str) -> dict[str, Any]:
    content = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, re.I)
    if fenced:
        content = fenced.group(1).strip()
    match = re.search(r"\{[\s\S]*\}", content)
    if not match:
        raise TripPatchError("Patch interpreter did not return JSON")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise TripPatchError("Patch JSON must be an object")
    return value


def poi_stable_id(day_position: int, poi_position: int, attraction: Attraction) -> str:
    return attraction.place_id or f"day:{day_position}:poi:{poi_position}"


def compact_plan_for_patch(plan: TripPlan) -> dict[str, Any]:
    return {
        "city": plan.city,
        "cities": plan.cities,
        "start_date": plan.start_date,
        "end_date": plan.end_date,
        "day_count": len(plan.days),
        "plan_version": plan.plan_version,
        "days": [{
            "day_index": position,
            "date": day.date,
            "city": day.city,
            "start_time": day.start_time,
            "transportation": day.transportation,
            "attractions": [{
                "stable_id": poi_stable_id(position, poi_position, poi),
                "name": poi.name,
                "category": poi.category,
                "visit_duration": poi.visit_duration,
            } for poi_position, poi in enumerate(day.attractions)],
            "meals": [{"type": meal.type, "name": meal.name} for meal in day.meals],
            "hotel": day.hotel.name if day.hotel else None,
        } for position, day in enumerate(plan.days)],
        "budget": plan.budget.model_dump(mode="json") if plan.budget else None,
    }


def _protected_constraints(request: TripRequest, plan: TripPlan) -> dict[str, Any]:
    profile = request.preference_profile
    return {
        "cities": plan.cities or [plan.city],
        "start_date": plan.start_date,
        "end_date": plan.end_date,
        "day_count": len(plan.days),
        "explicit_interests": profile.interests if profile else request.preferences,
        "earliest_start_time": (
            profile.constraints.earliest_start_time if profile else None
        ),
        "mobility_notes": profile.constraints.mobility_notes if profile else [],
        "budget_cny": profile.budget_cny if profile else None,
    }


class TripPatchInterpreter:
    def __init__(self, llm: Any = None):
        self.llm = llm

    def _completion(self, prompt: str) -> str:
        llm = self.llm or get_llm()
        response = create_chat_completion(
            stage="trip_patch",
            model=llm.model,
            messages=[{"role": "user", "content": prompt}],
            llm_instance=llm,
            temperature=0.0,
            max_tokens=PATCH_MAX_COMPLETION_TOKENS,
        )
        return response.choices[0].message.content or ""

    async def interpret(
        self, instruction: str, plan: TripPlan, request: TripRequest
    ) -> TripPatch:
        payload = {
            "instruction": instruction,
            "current_plan": compact_plan_for_patch(plan),
            "preference_constraints": _protected_constraints(request, plan),
            "allowed_operations": [
                "replace_poi", "remove_poi", "add_poi", "update_start_time",
                "update_transport", "update_meal", "update_day_pace",
            ],
            "trip_patch_json_schema": TripPatch.model_json_schema(),
        }
        prompt = (
            "Interpret exactly one user edit request as a strict TripPatch JSON. Do not return "
            "a TripPlan. Use only listed stable IDs. Never output place_id, coordinates, map "
            "source, verification metadata, risks, validation status, revision metadata, dates, "
            "cities, or internal IDs as editable values. For city/date/day-count changes, complete "
            "redesign, hotel replacement, or broad budget replanning, set requires_regeneration=true "
            "with zero operations. For a smaller budget request that cannot be achieved by one "
            "explicit local removal, require regeneration. affected/protected days are zero-based. "
            "JSON only.\nINPUT:\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        text = await asyncio.to_thread(self._completion, prompt)
        return TripPatch.model_validate(_json_object(text))


class TripPatchEngine:
    """Applies a validated operation union without letting the LLM touch TripPlan."""

    @staticmethod
    def calculate_affected_days(patch: TripPatch, day_count: int) -> list[int]:
        affected = sorted({operation.day_index for operation in patch.operations})
        if any(index < 0 or index >= day_count for index in affected):
            raise TripPatchError("Patch day_index is outside the current trip")
        return affected

    @staticmethod
    def _find_poi(plan: TripPlan, day_index: int, target_id: str, target_name: str) -> int:
        day = plan.days[day_index]
        for position, poi in enumerate(day.attractions):
            if poi_stable_id(day_index, position, poi) == target_id and poi.name == target_name:
                return position
        raise TripPatchError(f"Target POI not found: {target_name}")

    @staticmethod
    def _new_attraction(value: Any) -> Attraction:
        return Attraction(
            name=value.name,
            address="",
            location=Location(longitude=0.0, latitude=0.0),
            visit_duration=value.visit_duration,
            description=value.description or value.name,
            category=value.category,
            ticket_price=value.ticket_price,
            place_id="",
            poi_id="",
            poi_match_status="unverified",
            map_data_source="llm_unverified",
        )

    @staticmethod
    def _recalculate_budget(plan: TripPlan) -> None:
        if not plan.budget:
            return
        plan.budget.total_attractions = sum(
            poi.ticket_price for day in plan.days for poi in day.attractions
        )
        plan.budget.total_meals = sum(
            meal.estimated_cost for day in plan.days for meal in day.meals
        )
        plan.budget.total = (
            plan.budget.total_attractions + plan.budget.total_hotels
            + plan.budget.total_meals + plan.budget.total_transportation
            + plan.budget.total_inter_city_transport
        )

    def apply_patch(self, original: TripPlan, patch: TripPatch) -> tuple[TripPlan, list[int]]:
        if patch.requires_regeneration:
            if patch.operations:
                raise TripPatchError("Regeneration-only patch cannot contain operations")
            return original.model_copy(deep=True), []
        if not patch.operations:
            raise TripPatchError("Patch contains no operations")

        updated = original.model_copy(deep=True)
        affected = self.calculate_affected_days(patch, len(original.days))
        if sorted(set(patch.affected_day_indices)) != affected:
            raise TripPatchError("affected_day_indices do not match operations")
        expected_protected = [i for i in range(len(original.days)) if i not in affected]
        if sorted(set(patch.protected_day_indices)) != expected_protected:
            raise TripPatchError("protected_day_indices do not protect every unaffected day")

        for operation in patch.operations:
            day = updated.days[operation.day_index]
            if isinstance(operation, UpdateStartTimeOperation):
                if operation.old_value is not None and day.start_time != operation.old_value:
                    raise TripPatchError("start_time old_value is stale")
                day.start_time = operation.new_value
            elif isinstance(operation, UpdateTransportOperation):
                if operation.old_value is not None and day.transportation != operation.old_value:
                    raise TripPatchError("transport old_value is stale")
                day.transportation = operation.new_value
            elif isinstance(operation, RemovePOIOperation):
                position = self._find_poi(
                    updated, operation.day_index, operation.target_id, operation.target_name
                )
                day.attractions.pop(position)
            elif isinstance(operation, ReplacePOIOperation):
                position = self._find_poi(
                    updated, operation.day_index, operation.target_id, operation.target_name
                )
                day.attractions[position] = self._new_attraction(operation.new_poi)
            elif isinstance(operation, AddPOIOperation):
                day.attractions.append(self._new_attraction(operation.new_poi))
            elif isinstance(operation, UpdateMealOperation):
                matches = [
                    index for index, meal in enumerate(day.meals)
                    if meal.type == operation.meal_type
                    and (operation.target_name is None or meal.name == operation.target_name)
                ]
                if len(matches) != 1:
                    raise TripPatchError("Target meal was not uniquely found")
                value = operation.new_meal
                day.meals[matches[0]] = Meal(
                    type=value.type, name=value.name, description=value.description,
                    estimated_cost=value.estimated_cost,
                )
            elif isinstance(operation, UpdateDayPaceOperation):
                if len(day.attractions) < 2:
                    raise TripPatchError("Day is already too small to lighten safely")
                day.attractions.pop()
            else:  # pragma: no cover - discriminated schema prevents this
                raise TripPatchError("Unsupported patch operation")

        self._recalculate_budget(updated)
        protected_top_level = ("city", "cities", "start_date", "end_date")
        for field in protected_top_level:
            if getattr(updated, field) != getattr(original, field):
                raise TripPatchError(f"Protected field changed: {field}")
        if len(updated.days) != len(original.days):
            raise TripPatchError("Patch changed trip day count")
        for index in expected_protected:
            if updated.days[index].model_dump(mode="json") != original.days[index].model_dump(mode="json"):
                raise TripPatchError(f"Protected day changed: {index}")
        return TripPlan.model_validate(updated.model_dump(mode="json")), affected

    @staticmethod
    def compare_before_after(before: TripPlan, after: TripPlan) -> TripChangeDiff:
        changed_days: list[int] = []
        unchanged_days: list[int] = []
        changed_fields: list[str] = []
        added: list[str] = []
        removed: list[str] = []
        replaced: list[str] = []
        for index, (old_day, new_day) in enumerate(zip(before.days, after.days)):
            if old_day.model_dump(mode="json") == new_day.model_dump(mode="json"):
                unchanged_days.append(index)
                continue
            changed_days.append(index)
            for field in ("start_time", "transportation", "description", "meals", "hotel"):
                if getattr(old_day, field) != getattr(new_day, field):
                    changed_fields.append(f"days[{index}].{field}")
            old_names = [poi.name for poi in old_day.attractions]
            new_names = [poi.name for poi in new_day.attractions]
            if old_names != new_names:
                changed_fields.append(f"days[{index}].attractions")
                for position in range(min(len(old_names), len(new_names))):
                    if old_names[position] != new_names[position]:
                        replaced.append(f"{old_names[position]} → {new_names[position]}")
                removed.extend(name for name in old_names if name not in new_names)
                added.extend(name for name in new_names if name not in old_names)
        return TripChangeDiff(
            changed_day_indices=changed_days,
            changed_fields=changed_fields,
            added_pois=added,
            removed_pois=removed,
            replaced_pois=replaced,
            unchanged_day_indices=unchanged_days,
        )

    @staticmethod
    def change_summary(diff: TripChangeDiff) -> list[str]:
        summary = [f"已修改第 {index + 1} 天" for index in diff.changed_day_indices]
        summary.extend(diff.replaced_pois)
        summary.extend(f"新增：{name}" for name in diff.added_pois if not any(name in item for item in diff.replaced_pois))
        summary.extend(f"移除：{name}" for name in diff.removed_pois if not any(name in item for item in diff.replaced_pois))
        if diff.unchanged_day_indices:
            days = "、".join(str(index + 1) for index in diff.unchanged_day_indices)
            summary.append(f"第 {days} 天未修改")
        return summary


_trip_patch_interpreter: Optional[TripPatchInterpreter] = None


def get_trip_patch_interpreter() -> TripPatchInterpreter:
    global _trip_patch_interpreter
    if _trip_patch_interpreter is None:
        _trip_patch_interpreter = TripPatchInterpreter()
    return _trip_patch_interpreter
