"""Order processing system fixture for benchmark evaluation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class OrderItem:
    item_id: str
    quantity: int
    unit_price_cents: int


@dataclass
class Order:
    order_id: str
    customer_id: str
    items: list[OrderItem] = field(default_factory=list)
    currency: str = "USD"
    discount_cents: int = 0
    status: str = "pending"
    created_at: float = field(default_factory=time.time)

    @property
    def subtotal_cents(self) -> int:
        return sum(item.quantity * item.unit_price_cents for item in self.items)

    @property
    def total_cents(self) -> int:
        return max(0, self.subtotal_cents - self.discount_cents)


class OrderProcessor:
    def __init__(self, tax_rate_basis_points: int = 825) -> None:
        self.tax_rate_basis_points = tax_rate_basis_points
        self.processed_orders: dict[str, Order] = {}

    def calculate_tax(self, order: Order) -> int:
        # Basis points: 825 = 8.25%
        return (order.total_cents * self.tax_rate_basis_points) // 10000

    def process_order(self, order: Order) -> dict[str, int | str]:
        if not order.items:
            raise ValueError("Order must contain at least one item")
        if order.status != "pending":
            raise ValueError(f"Cannot process order in status {order.status}")

        tax = self.calculate_tax(order)
        final_total = order.total_cents + tax
        order.status = "completed"
        self.processed_orders[order.order_id] = order

        return {
            "order_id": order.order_id,
            "subtotal": order.subtotal_cents,
            "tax": tax,
            "final_total": final_total,
            "status": order.status,
        }
