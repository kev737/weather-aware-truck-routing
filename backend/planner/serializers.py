from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

MAX_DAYS_AHEAD = 7  # forecasts get unreliable (and ETAs run past the horizon) beyond this


class PlaceField(serializers.Field):
    """Free text ("Dallas, TX", "32.77, -96.79") or a picked suggestion {name, lat, lon}."""

    def to_internal_value(self, data):
        if isinstance(data, str):
            if not data.strip():
                raise serializers.ValidationError("Enter a place.")
            return data.strip()
        if isinstance(data, dict):
            try:
                lat, lon = float(data["lat"]), float(data["lon"])
            except (KeyError, TypeError, ValueError):
                raise serializers.ValidationError("A place needs numeric lat and lon.")
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise serializers.ValidationError("Coordinates are out of range.")
            return {"name": str(data.get("name", ""))[:200], "lat": lat, "lon": lon}
        raise serializers.ValidationError("Expected text or {name, lat, lon}.")

    def to_representation(self, value):
        return value


class PlanRequestSerializer(serializers.Serializer):
    origin = PlaceField()
    destination = PlaceField()
    departure = serializers.DateTimeField(help_text="ISO 8601 with a UTC offset")
    load_lb = serializers.IntegerField(min_value=0, max_value=80_000)
    interval_mi = serializers.ChoiceField(choices=[10, 25, 50], default=25)

    def validate_departure(self, value):
        now = timezone.now()
        if value < now - timedelta(hours=1):
            raise serializers.ValidationError("Departure can't be in the past.")
        if value > now + timedelta(days=MAX_DAYS_AHEAD):
            raise serializers.ValidationError(f"Departure must be within {MAX_DAYS_AHEAD} days.")
        return value
