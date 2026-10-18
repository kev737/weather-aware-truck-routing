from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

MAX_DAYS_AHEAD = 7  # forecasts get unreliable (and ETAs run past the horizon) beyond this


class PlaceField(serializers.Field):
    """Free text ("Dallas, TX", "32.77, -96.79") or a picked suggestion {name, lat, lon}."""

    def to_internal_value(self, data):
        if isinstance(data, str):
            if not data.strip():
                raise serializers.ValidationError("Enter a city or address.")
            return data.strip()
        if isinstance(data, dict):
            try:
                lat, lon = float(data["lat"]), float(data["lon"])
            except (KeyError, TypeError, ValueError):
                raise serializers.ValidationError("We couldn't read that location. Type a city or pick a suggestion.")
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise serializers.ValidationError("Those coordinates aren't on the map. Check the numbers or type a city.")
            return {"name": str(data.get("name", ""))[:200], "lat": lat, "lon": lon}
        raise serializers.ValidationError("We couldn't read that location. Type a city or pick a suggestion.")

    def to_representation(self, value):
        return value


class PlanRequestSerializer(serializers.Serializer):
    origin = PlaceField()
    destination = PlaceField()
    departure = serializers.DateTimeField(
        help_text="ISO 8601 with a UTC offset",
        error_messages={"invalid": "Pick a departure date and time.", "required": "Pick a departure date and time."},
    )
    load_lb = serializers.IntegerField(
        min_value=0,
        max_value=80_000,
        error_messages={
            key: "Enter 0 to 80,000 lb." for key in ("invalid", "required", "min_value", "max_value")
        },
    )
    interval_mi = serializers.ChoiceField(
        choices=[10, 25, 50], default=25, error_messages={"invalid_choice": "Check the weather every 10, 25 or 50 miles."}
    )

    def validate_departure(self, value):
        now = timezone.now()
        if value < now - timedelta(hours=1):
            raise serializers.ValidationError("That time has passed. Pick a departure from now on.")
        if value > now + timedelta(days=MAX_DAYS_AHEAD):
            raise serializers.ValidationError(f"You can plan up to {MAX_DAYS_AHEAD} days ahead. Pick an earlier departure.")
        return value
