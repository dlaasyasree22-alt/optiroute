"""
OptiRoute Solver Module
"""

from solver.optimizer import solve_allocation, load_optimizer_data
from solver.fefo_repair import repair_fefo_violations, assert_zero_fefo_violations
from solver.counterfactual import why_not_query
from solver.ablation import run_benchmark_ablation, generate_ablation_demands

__all__ = [
    "solve_allocation",
    "load_optimizer_data",
    "repair_fefo_violations",
    "assert_zero_fefo_violations",
    "why_not_query",
    "run_benchmark_ablation",
    "generate_ablation_demands",
]
