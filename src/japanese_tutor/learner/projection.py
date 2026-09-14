"""Deterministic descriptive state; no statistical mastery or automatic decay."""

from collections import defaultdict

PROJECTION_VERSION = "recent-activities-v1"
WINDOW = 5


def project(rows: list[dict]) -> list[dict]:
    """Rows contain effective observations in observed-time/sequence order.

    Count the earliest sufficiently supported attempt per activity and dimension.
    Retries remain visible but cannot turn one practice opportunity into many successes.
    """
    scopes = defaultdict(list)
    for row in rows:
        scopes[(row["concept_id"], row["dimension"])].append(row)
    states = []
    for (concept_id, dimension), observations in sorted(scopes.items()):
        activities = {}
        hinted_activities = set()
        supported = []
        for item in observations:
            if item["confidence"] == "sufficient":
                activities.setdefault(item["activity_id"], item)
                supported.append(item)
                if item["hint_used"]:
                    hinted_activities.add(item["activity_id"])
        recent = list(activities.values())[-WINDOW:]
        successes = sum(item["result"] == "independent" for item in recent)
        dates = {item["observed_at"][:10] for item in recent if item["result"] == "independent"}
        if len(recent) < 2:
            status = "insufficient_evidence"
        elif recent[-1]["result"] != "independent" or supported[-1]["result"] != "independent":
            status = "needs_practice"
        elif (
            len(recent) >= 3
            and all(item["result"] == "independent" for item in recent[-3:])
            and len({item["observed_at"][:10] for item in recent[-3:]}) >= 2
        ):
            status = "consistent_recently"
        else:
            status = "developing"
        last = observations[-1]
        states.append(
            {
                "concept_id": concept_id,
                "lesson_id": last["lesson_id"],
                "dimension": dimension,
                "status": status,
                "effective_observation_count": len(observations),
                "sufficient_activity_count": len(activities),
                "recent_activity_count": len(recent),
                "recent_independent_successes": successes,
                "recent_independent_dates": len(dates),
                "recent_hint_dependency_count": sum(
                    item["activity_id"] in hinted_activities for item in recent
                ),
                "recent_results": [item["result"] for item in recent],
                "recent_errors": list(
                    dict.fromkeys(code for item in recent for code in item["error_codes"])
                )[:20],
                "last_observed_at": last["observed_at"],
                "last_supported_at": supported[-1]["observed_at"] if supported else None,
                "last_attempt_result": last["result"],
                "last_attempt_confidence": last["confidence"],
                "projection_version": PROJECTION_VERSION,
            }
        )
    return states
