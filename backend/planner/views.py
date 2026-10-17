import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .serializers import PlanRequestSerializer
from .services import geocoding, planner, routing
from .services.http import RateLimited, UpstreamError

log = logging.getLogger(__name__)

RETRY_AFTER_S = 60


def _upstream_failed(exc: UpstreamError) -> Response:
    log.warning("upstream failure: %s", exc)
    body = {"detail": f"The {exc.service} service failed: {exc.message}"}
    if isinstance(exc, RateLimited):
        body["retry_after_s"] = RETRY_AFTER_S
        return Response(body, status=status.HTTP_503_SERVICE_UNAVAILABLE, headers={"Retry-After": str(RETRY_AFTER_S)})
    return Response(body, status=status.HTTP_502_BAD_GATEWAY)


@api_view(["POST"])
def create_trip(request):
    """Step 1: three truck routes and an estimate of the weather download."""
    s = PlanRequestSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    try:
        _, summary = planner.create_trip(planner.TripRequest(**s.validated_data))
    except (geocoding.PlaceNotFound, planner.SamePlace, routing.NoRoute) as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except UpstreamError as exc:
        return _upstream_failed(exc)
    return Response(summary, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def trip_plan(request, trip_id: str):
    """Step 2: weather risk at each checkpoint's ETA, the recommendation, departure window and heatmap."""
    try:
        return Response(planner.plan_trip(planner.load_trip(trip_id)))
    except planner.TripNotFound as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    except UpstreamError as exc:
        return _upstream_failed(exc)


@api_view(["GET"])
def geocode(request):
    """Place suggestions for the origin/destination inputs."""
    try:
        places = geocoding.search(request.query_params.get("q", ""), limit=5)
    except UpstreamError as exc:
        return _upstream_failed(exc)
    return Response([p.as_dict() for p in places])


@api_view(["GET"])
def health(request):
    return Response({"ok": True})
