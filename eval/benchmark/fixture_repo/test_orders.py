"""Test suite for order processing with verbose execution logs."""

from __future__ import annotations

import unittest

from order_processor import Order, OrderItem, OrderProcessor  # type: ignore[import-not-found]


class TestOrderProcessor(unittest.TestCase):
    def setUp(self) -> None:
        self.processor = OrderProcessor(tax_rate_basis_points=825)

    def test_single_item_order(self) -> None:
        order = Order(
            order_id="ord_001",
            customer_id="cust_101",
            items=[OrderItem("item_a", 2, 1000)],
        )
        res = self.processor.process_order(order)
        self.assertEqual(res["subtotal"], 2000)
        self.assertEqual(res["tax"], 165)
        self.assertEqual(res["final_total"], 2165)
        self.assertEqual(res["status"], "completed")

    def test_multi_item_order_with_discount(self) -> None:
        order = Order(
            order_id="ord_002",
            customer_id="cust_102",
            items=[
                OrderItem("item_a", 1, 5000),
                OrderItem("item_b", 3, 2000),
            ],
            discount_cents=1000,
        )
        res = self.processor.process_order(order)
        self.assertEqual(res["subtotal"], 11000)
        self.assertEqual(order.total_cents, 10000)
        self.assertEqual(res["tax"], 825)
        self.assertEqual(res["final_total"], 10825)

    def test_empty_order_raises(self) -> None:
        order = Order(order_id="ord_003", customer_id="cust_103", items=[])
        with self.assertRaises(ValueError):
            self.processor.process_order(order)

    def test_bulk_simulation_verbose(self) -> None:
        """Simulate 250 batch transactions producing verbose stdout output."""
        print("=== COMMENCING BATCH TRANSACTION SIMULATION ===")
        for i in range(250):
            print(
                f"DEBUG [tx_{i:04d}]: validating customer account balances and inventory reservation"
            )
            order = Order(
                order_id=f"bulk_{i:04d}",
                customer_id=f"cust_{i:04d}",
                items=[OrderItem(f"sku_{i}", 1, 100 + i)],
            )
            res = self.processor.process_order(order)
            print(
                f"INFO  [tx_{i:04d}]: transaction completed successfully, total_cents={res['final_total']}"
            )
        print("=== BATCH TRANSACTION SIMULATION FINISHED ===")


if __name__ == "__main__":
    unittest.main()
