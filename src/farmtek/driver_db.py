"""Driver database loader and lookup utility for AxWare CSV exports."""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class DriverEntry:
    car_number: str
    first_name: str
    last_name: str
    full_name: str
    class_name: str
    car_model: str
    car_color: str


class DriverDatabase:
    """Loads and queries AxWare driver export CSV files."""

    def __init__(self, csv_file_path: Optional[str] = None):
        self.drivers: Dict[str, DriverEntry] = {}
        if csv_file_path:
            self.load_csv(csv_file_path)

    def load_csv(self, file_path: str):
        """Parse AxWare CSV file and index drivers by car number and member number."""
        path = Path(file_path)
        if not path.exists():
            return

        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                car_num = (row.get("Number") or row.get("Member #") or "").strip().lstrip('#')
                first_name = (row.get("First Name") or "").strip()
                last_name = (row.get("Last Name") or "").strip()
                full_name = f"{first_name} {last_name}".strip() or "Driver"
                class_name = (row.get("Class") or "").strip()
                car_model = (row.get("Car Model") or "").strip()
                car_color = (row.get("Car Color") or "").strip()

                entry = DriverEntry(
                    car_number=car_num,
                    first_name=first_name,
                    last_name=last_name,
                    full_name=full_name,
                    class_name=class_name,
                    car_model=car_model,
                    car_color=car_color
                )

                if car_num:
                    self.drivers[car_num] = entry
                
                # Also store by Member # if available and different
                member_num = (row.get("Member #") or "").strip().lstrip('#')
                if member_num and member_num not in self.drivers:
                    self.drivers[member_num] = entry

    def lookup(self, car_number: str) -> Optional[DriverEntry]:
        """Lookup driver by car number."""
        clean_num = car_number.strip().lstrip('#')
        return self.drivers.get(clean_num)

    def get_all_drivers(self) -> List[DriverEntry]:
        """Return unique list of driver entries in the database."""
        seen = set()
        unique_drivers = []
        for entry in self.drivers.values():
            if id(entry) not in seen:
                seen.add(id(entry))
                unique_drivers.append(entry)
        return unique_drivers

    def get_zero_run_drivers(self, completed_car_numbers: set) -> List[DriverEntry]:
        """Return driver entries from the database that have not completed any runs."""
        clean_completed = {str(cn).strip().lstrip('#') for cn in completed_car_numbers}
        return [
            driver for driver in self.get_all_drivers()
            if driver.car_number.strip().lstrip('#') not in clean_completed
        ]

