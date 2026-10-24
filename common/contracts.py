from dataclasses import dataclass
from typing import List


@dataclass
class TargetDemand:
    sku_id: str
    store_id: str
    target_q_demand: float
    p90_demand: float
    p50_demand: float
    projected_stock_delivery: float


@dataclass
class ShipmentLine:
    sku_id: str
    batch_id: str
    warehouse_id: str
    store_id: str
    cases_shipped: int
    units_shipped: int
    truck_id: str
    days_to_expiry_at_arrival: int
    reason_code: str


@dataclass
class PlanResult:
    plan_id: str
    solver_status: str
    solve_time_seconds: float
    total_freight_cost: float
    unmet_demand_units: float
    wastage_units: float
    truck_utilization_pct: float
    shipments: List[ShipmentLine]