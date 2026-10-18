import logging

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .serializers import PlanRequestSerializer
from .services import geocoding, planner, quota, routing
from .services.http import RateLimited, UpstreamError
from .services.quota import QuotaExhausted

log = logging.getLogger(__name__)

RETRY_AFTER_S = 60

# How the third-party services are named to customers; the technical detail goes to the log.
SERVICE_NAMES = {"routing": "the route planner", "geocoding": "place search", "weather": "the weather service"}


def _upstream_failed(exc: UpstreamError) -> Response:
    log.warning("upstream failure: %s", exc)
    name = SERVICE_NAMES.get(exc.service, "a service we use")
    body = {"detail": f"We couldn't reach {name} just now. Please try again in a minute."}
    if isinstance(exc, QuotaExhausted):
        # No retry_after: the browser shouldn't keep trying until the quota resets.
        body["detail"] = exc.message[0].upper() + exc.message[1:]
        body["quota_exhausted"] = True
        return Response(body, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    if isinstance(exc, RateLimited):
        wait = exc.retry_after_s or RETRY_AFTER_S
        body["detail"] = f"{name[0].upper()}{name[1:]} is busy. Trying again in about {wait} s."
        body["retry_after_s"] = wait
        return Response(body, status=status.HTTP_503_SERVICE_UNAVAILABLE, headers={"Retry-After": str(wait)})
    return Response(body, status=status.HTTP_502_BAD_GATEWAY)


@api_view(["POST"])
def create_trip(request):
    """Step 1: three truck routes and an estimate of the weather download."""
    s = PlanRequestSerializer(data=request.data)
    s.is_valid(raise_exception=True)
    try:
        _, summary = planner.create_trip(planner.TripRequest(**s.validated_data))
    except geocoding.PlaceNotFound as exc:
        return Response({exc.field or "detail": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
    except planner.SamePlace as exc:
        return Response({"destination": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
    except routing.NoRoute as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except UpstreamError as exc:
        return _upstream_failed(exc)
    return Response(summary, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def trip_detail(request, trip_id: str):
    """Step 1, polled: the routes found so far, until `routes_complete`."""
    try:
        return Response(planner.trip_summary(planner.load_trip(trip_id)))
    except planner.TripNotFound as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)


@api_view(["GET"])
def trip_plan(request, trip_id: str):
    """Step 2: weather risk at each checkpoint's ETA, the recommendation, departure window and heatmap."""
    try:
        trip = planner.load_trip(trip_id)
    except planner.TripNotFound as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)
    if not trip.routes_complete:
        return Response({"detail": "Still finding routes; try again in a moment."}, status=status.HTTP_409_CONFLICT)
    try:
        return Response(planner.plan_trip(trip))
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
def usage(request):
    """How much of Open-Meteo's free tier this server has used (per minute, hour, day)."""
    return Response(quota.status())


@api_view(["GET"])
def health(request):
    return Response({"ok": True})
