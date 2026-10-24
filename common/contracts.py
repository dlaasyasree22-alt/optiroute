from dataclasses import dataclass, field
from typing import List


@dataclass
class TargetDemand:
    sku_id: str
    store_id: str
    target_q_demand: float
    p90_demand: float
    p50_demand: float
    projected_stock_delivery: float
    # Quantities above are totals over the planning horizon
    horizon_days: int = 1
    on_hand_units: float = 0.0


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
    total_demand_units: float = 0.0
    stockout_cost: float = 0.0
    writeoff_cost: float = 0.0
    trucks_used: int = 0
    mip_gap: float = 0.0
