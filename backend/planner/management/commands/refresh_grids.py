from django.core.management.base import BaseCommand

from planner.services import grids


class Command(BaseCommand):
    help = "Download any newer HRRR / GFS run into the local forecast grids (run hourly)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="rebuild the current runs even if unchanged")

    def handle(self, *args, **options):
        result = grids.refresh(force=options["force"])
        current = grids.Grids.current().describe()
        self.stdout.write(f"updated: {result}  current: {current}")
