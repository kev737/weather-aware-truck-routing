from django.urls import path

from . import views

urlpatterns = [
    path("trips/", views.create_trip, name="create-trip"),
    path("trips/<str:trip_id>/plan/", views.trip_plan, name="trip-plan"),
    path("geocode/", views.geocode, name="geocode"),
    path("usage/", views.usage, name="usage"),
    path("health/", views.health, name="health"),
]
